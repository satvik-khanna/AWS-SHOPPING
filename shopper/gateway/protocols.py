"""Unified 1-URL -> MCP + UCP + ACP + AI Discovery adapter backed by shopper.Deps."""
from __future__ import annotations

import html
import json
import secrets
from typing import Any

from ..models import LandedCost, Listing, RankedPick, ShoppingRequest
from ..pipeline import Deps
from .interfaces import StoreRegistry
from .models import CheckoutSession, StoreProfile

UCP_VERSION = "2026-08-25"
ACP_VERSION = "2026-04-17"
MCP_PROTOCOL_VERSION = "2025-06-18"


def _usd_to_cents(amount: float) -> int:
    return int(round(amount * 100))


def _split_cost_lines(cost: LandedCost) -> tuple[float, float, float]:
    """Return (subtotal_usd, shipping_usd, tax_duty_usd) from itemized CostLines."""
    subtotal = 0.0
    shipping = 0.0
    tax_duty = 0.0
    for line in cost.lines:
        lbl = line.label.lower()
        if "ship" in lbl:
            shipping += line.amount_usd
        elif "duty" in lbl or "tax" in lbl:
            tax_duty += line.amount_usd
        else:
            subtotal += line.amount_usd
    return round(subtotal, 2), round(shipping, 2), round(tax_duty, 2)


class UnifiedCommerceAdapter:
    """Translates shopper Deps + registered stores into MCP, UCP, ACP, and discovery."""

    def __init__(self, deps: Deps, registry: StoreRegistry):
        self.deps = deps
        self.registry = registry
        self._sessions: dict[str, CheckoutSession] = {}
        self._idempotency: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Shared catalog & checkout core
    # ------------------------------------------------------------------
    def _resolve_store(self, store_slug: str | None) -> StoreProfile:
        if store_slug:
            s = self.registry.get(store_slug)
            if s is not None:
                return s
        stores = self.registry.list_all()
        if stores:
            return stores[0]
        return StoreProfile(slug="default", name="AWS-SHOPPING", source_url="https://example.com")

    def _catalog(self, store: StoreProfile, query: str = "", max_total_usd: float = 10_000.0,
                 ship_to: str | None = None, user_id: str = "default") -> list[RankedPick]:
        q = query or store.query_filter or ""
        dest = ship_to or store.default_ship_to or "US-CA"
        req = ShoppingRequest(query=q, max_total_usd=max_total_usd, ship_to=dest, user_id=user_id)
        prefs = self.deps.memory.recall(user_id)
        listings = [l for l in self.deps.source.search(req, prefs) if l.seller not in prefs.rejected_sellers]
        candidates: list[tuple[Listing, LandedCost]] = []
        for l in listings:
            c = self.deps.cost.landed_cost(l, req)
            if c.blocked_reason or c.total_usd > max_total_usd:
                continue
            candidates.append((l, c))
        return self.deps.ranker.rank(req, prefs, candidates)

    def _find_pick(self, store: StoreProfile, listing_id: str, ship_to: str) -> RankedPick:
        picks = self._catalog(store, query="", max_total_usd=1_000_000.0, ship_to=ship_to)
        for p in picks:
            if p.listing.id == listing_id:
                return p
        raise KeyError(f"listing not found or not shippable: {listing_id}")

    def _open_session(self, store: StoreProfile, listing_id: str, quantity: int = 1,
                      ship_to: str | None = None, buyer: dict[str, str] | None = None) -> CheckoutSession:
        dest = ship_to or store.default_ship_to or "US-CA"
        pick = self._find_pick(store, listing_id, dest)
        cart = self.deps.checkout.fill_cart(pick)
        if cart.status != "awaiting_approval":
            raise ValueError(f"checkout blocked: {cart.detail}")
        qty = max(1, int(quantity))
        sub, ship, tax = _split_cost_lines(pick.cost)
        sub = round(sub * qty, 2)
        ship = round(ship * qty, 2)
        tax = round(tax * qty, 2)
        total = round(sub + ship + tax, 2)
        sid = f"chk_{secrets.token_hex(8)}"
        sess = CheckoutSession(
            id=sid,
            store_slug=store.slug,
            listing_id=pick.listing.id,
            title=pick.listing.title_en,
            quantity=qty,
            ship_to=dest,
            currency="USD",
            subtotal_usd=sub,
            shipping_usd=ship,
            tax_duty_usd=tax,
            total_usd=total,
            status="awaiting_approval",
            approval_token=cart.approval_token,
            cart_url=cart.cart_url,
            buyer=dict(buyer or {}),
            citations=[
                {"label": ln.label, "amount_usd": f"{ln.amount_usd:.2f}",
                 "source": ln.citation.source, "as_of": ln.citation.as_of}
                for ln in pick.cost.lines
            ],
        )
        self._sessions[sid] = sess
        return sess

    def _complete_session(self, session_id: str, approval_token: str | None) -> CheckoutSession:
        sess = self._sessions.get(session_id)
        if sess is None:
            raise KeyError(f"unknown session: {session_id}")
        if sess.status != "awaiting_approval":
            raise ValueError(f"session not awaiting approval (status={sess.status})")
        if not approval_token or approval_token != sess.approval_token:
            raise PermissionError("invalid or missing single-use approval token")
        res = self.deps.checkout.approve(approval_token)
        if res.status != "placed":
            raise PermissionError(f"approval rejected: {res.detail}")
        sess.status = "completed"
        sess.approval_token = None
        sess.order_id = f"ord_{secrets.token_hex(6)}"
        return sess

    def get_raw_session(self, session_id: str) -> CheckoutSession | None:
        return self._sessions.get(session_id)

    # ------------------------------------------------------------------
    # 1. MCP (Model Context Protocol — JSON-RPC 2.0 over Streamable HTTP)
    # ------------------------------------------------------------------
    def _mcp_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_catalog",
                "description": "Search store listings with cited landed-cost quotes (USD).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Product query in English"},
                        "max_total_usd": {"type": "number", "default": 1000},
                        "ship_to": {"type": "string", "default": "US-CA"},
                    },
                },
            },
            {
                "name": "get_landed_cost",
                "description": "Get itemized landed cost with rule citations for a listing_id.",
                "inputSchema": {
                    "type": "object",
                    "required": ["listing_id"],
                    "properties": {
                        "listing_id": {"type": "string"},
                        "ship_to": {"type": "string", "default": "US-CA"},
                    },
                },
            },
            {
                "name": "create_checkout",
                "description": "Fill cart and pause at awaiting_approval (never pays without approval).",
                "inputSchema": {
                    "type": "object",
                    "required": ["listing_id"],
                    "properties": {
                        "listing_id": {"type": "string"},
                        "quantity": {"type": "integer", "default": 1},
                        "ship_to": {"type": "string", "default": "US-CA"},
                    },
                },
            },
            {
                "name": "approve_checkout",
                "description": "Finalize a paused checkout using its single-use approval_token.",
                "inputSchema": {
                    "type": "object",
                    "required": ["session_id", "approval_token"],
                    "properties": {
                        "session_id": {"type": "string"},
                        "approval_token": {"type": "string"},
                    },
                },
            },
            {
                "name": "register_store",
                "description": "Register any creator/shop URL to mint a 1-URL MCP+UCP+ACP storefront.",
                "inputSchema": {
                    "type": "object",
                    "required": ["name", "source_url"],
                    "properties": {
                        "slug": {"type": "string"},
                        "name": {"type": "string"},
                        "source_url": {"type": "string"},
                        "bio": {"type": "string"},
                        "query_filter": {"type": "string"},
                    },
                },
            },
        ]

    def handle_mcp(self, rpc: dict[str, Any], store_slug: str | None = None) -> dict[str, Any]:
        req_id = rpc.get("id")
        method = rpc.get("method", "")
        params = rpc.get("params") or {}
        store = self._resolve_store(store_slug)

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": f"aws-shopping-gateway:{store.slug}",
                        "version": "0.1.0",
                    },
                },
            }

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self._mcp_tools()}}

        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                out = self._dispatch_mcp_tool(store, name, args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                        "structuredContent": out,
                        "isError": False,
                    },
                }
            except (KeyError, ValueError, PermissionError) as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def _dispatch_mcp_tool(self, store: StoreProfile, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "search_catalog":
            picks = self._catalog(
                store,
                query=args.get("query", ""),
                max_total_usd=float(args.get("max_total_usd", 1000)),
                ship_to=args.get("ship_to"),
            )
            return {
                "store": store.slug,
                "items": [
                    {
                        "listing_id": p.listing.id,
                        "title_en": p.listing.title_en,
                        "title_ja": p.listing.title_ja,
                        "condition": p.listing.condition,
                        "seller": p.listing.seller,
                        "landed_total_usd": p.cost.total_usd,
                        "reason": p.reason,
                    }
                    for p in picks
                ],
            }

        if name == "get_landed_cost":
            dest = args.get("ship_to") or store.default_ship_to
            pick = self._find_pick(store, args["listing_id"], dest)
            return {
                "listing_id": pick.listing.id,
                "ship_to": dest,
                "total_usd": pick.cost.total_usd,
                "lines": [
                    {
                        "label": ln.label,
                        "amount_usd": ln.amount_usd,
                        "source": ln.citation.source,
                        "as_of": ln.citation.as_of,
                    }
                    for ln in pick.cost.lines
                ],
            }

        if name == "create_checkout":
            sess = self._open_session(
                store,
                listing_id=args["listing_id"],
                quantity=int(args.get("quantity", 1)),
                ship_to=args.get("ship_to"),
            )
            return {
                "session_id": sess.id,
                "status": sess.status,
                "listing_id": sess.listing_id,
                "total_usd": sess.total_usd,
                "approval_token": sess.approval_token,
                "cart_url": sess.cart_url,
            }

        if name == "approve_checkout":
            sess = self._complete_session(args["session_id"], args.get("approval_token"))
            return {
                "session_id": sess.id,
                "status": sess.status,
                "order_id": sess.order_id,
                "total_usd": sess.total_usd,
            }

        if name == "register_store":
            prof = self.registry.register(
                StoreProfile(
                    slug=args.get("slug", ""),
                    name=args["name"],
                    source_url=args["source_url"],
                    bio=args.get("bio", ""),
                    query_filter=args.get("query_filter", ""),
                )
            )
            return {"slug": prof.slug, "path": f"/s/{prof.slug}", "source_url": prof.source_url}

        raise KeyError(f"unknown tool: {name}")

    # ------------------------------------------------------------------
    # 2. UCP (Universal Commerce Protocol — Google / Shopify 2026 spec)
    # ------------------------------------------------------------------
    def ucp_manifest(self, base_url: str, store_slug: str | None = None) -> dict[str, Any]:
        base = base_url.rstrip("/")
        prefix = f"{base}/s/{store_slug}" if store_slug else base
        return {
            "ucp": {
                "version": UCP_VERSION,
                "services": {
                    "dev.ucp.shopping": [
                        {
                            "version": UCP_VERSION,
                            "spec": f"https://ucp.dev/{UCP_VERSION}/specification/overview",
                            "transport": "rest",
                            "endpoint": f"{prefix}/ucp",
                        },
                        {
                            "version": UCP_VERSION,
                            "spec": f"https://ucp.dev/{UCP_VERSION}/specification/overview",
                            "transport": "mcp",
                            "endpoint": f"{prefix}/mcp",
                        },
                    ]
                },
                "capabilities": {
                    "dev.ucp.shopping.checkout": [
                        {
                            "version": UCP_VERSION,
                            "spec": f"https://ucp.dev/{UCP_VERSION}/specification/shopping/checkout",
                        }
                    ]
                },
                "payment_handlers": {
                    "dev.aws_shopping.approval_escrow": [
                        {
                            "id": "approval_escrow_v1",
                            "version": UCP_VERSION,
                            "requires_human_approval": True,
                        }
                    ]
                },
            }
        }

    def _to_ucp(self, sess: CheckoutSession) -> dict[str, Any]:
        status_map = {
            "awaiting_approval": "ready_for_complete",
            "completed": "completed",
            "canceled": "canceled",
        }
        payload: dict[str, Any] = {
            "id": sess.id,
            "status": status_map.get(sess.status, "incomplete"),
            "currency": sess.currency,
            "line_items": [
                {
                    "id": sess.listing_id,
                    "title": sess.title,
                    "quantity": sess.quantity,
                    "unit_amount": sess.subtotal_usd,
                }
            ],
            "totals": {
                "subtotal": sess.subtotal_usd,
                "shipping": sess.shipping_usd,
                "tax": sess.tax_duty_usd,
                "total": sess.total_usd,
            },
            "continue_url": sess.cart_url or f"/s/{sess.store_slug}",
            "citations": sess.citations,
        }
        if sess.buyer:
            payload["buyer"] = sess.buyer
        if sess.approval_token:
            payload["payment"] = {"approval_token": sess.approval_token}
        if sess.order_id:
            payload["order"] = {
                "id": sess.order_id,
                "permalink_url": f"/s/{sess.store_slug}?order={sess.order_id}",
            }
        return payload

    def ucp_create_session(self, payload: dict[str, Any], store_slug: str | None = None) -> dict[str, Any]:
        store = self._resolve_store(store_slug)
        items = payload.get("line_items") or []
        if not items:
            raise ValueError("line_items must contain at least 1 item")
        first = items[0]
        ship_to = (payload.get("fulfillment") or {}).get("ship_to") or payload.get("ship_to")
        sess = self._open_session(
            store,
            listing_id=first["id"],
            quantity=int(first.get("quantity", 1)),
            ship_to=ship_to,
            buyer=payload.get("buyer"),
        )
        return self._to_ucp(sess)

    def ucp_get_session(self, session_id: str) -> dict[str, Any] | None:
        sess = self._sessions.get(session_id)
        return self._to_ucp(sess) if sess else None

    def ucp_complete_session(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        payment = payload.get("payment") or {}
        token = payment.get("approval_token") or payload.get("approval_token")
        sess = self._complete_session(session_id, token)
        return self._to_ucp(sess)

    def ucp_cancel_session(self, session_id: str) -> dict[str, Any]:
        sess = self._sessions.get(session_id)
        if sess is None:
            raise KeyError(f"unknown session: {session_id}")
        if sess.status == "completed":
            raise ValueError("cannot cancel a completed session")
        sess.status = "canceled"
        sess.approval_token = None
        return self._to_ucp(sess)

    # ------------------------------------------------------------------
    # 3. ACP (Agentic Commerce Protocol — OpenAI / Stripe 2026 spec)
    # ------------------------------------------------------------------
    def _to_acp(self, sess: CheckoutSession) -> dict[str, Any]:
        status_map = {
            "awaiting_approval": "ready_for_payment",
            "completed": "completed",
            "canceled": "canceled",
        }
        sub_c = _usd_to_cents(sess.subtotal_usd)
        ship_c = _usd_to_cents(sess.shipping_usd)
        tax_c = _usd_to_cents(sess.tax_duty_usd)
        tot_c = _usd_to_cents(sess.total_usd)
        payload: dict[str, Any] = {
            "id": sess.id,
            "status": status_map.get(sess.status, "not_ready_for_payment"),
            "currency": sess.currency.lower(),
            "line_items": [
                {
                    "id": sess.listing_id,
                    "quantity": sess.quantity,
                    "base_amount": sub_c,
                    "discount": 0,
                    "subtotal": sub_c,
                    "tax": tax_c,
                    "total": sub_c + tax_c,
                }
            ],
            "totals": {
                "subtotal": sub_c,
                "tax": tax_c,
                "shipping": ship_c,
                "discount": 0,
                "total": tot_c,
            },
            "fulfillment_options": [
                {
                    "id": "intl_proxy_standard",
                    "name": f"Proxy International Shipping ({sess.ship_to})",
                    "description": "Landed-cost verified proxy fulfillment",
                    "amount": ship_c,
                }
            ],
            "messages": [
                {
                    "type": "info",
                    "code": "approval_required",
                    "content": "Requires explicit user approval token before payment.",
                }
            ],
            "links": {
                "storefront": f"/s/{sess.store_slug}",
            },
        }
        if sess.approval_token:
            payload["approval_token"] = sess.approval_token
        if sess.order_id:
            payload["order"] = {"id": sess.order_id, "status": "placed"}
        return payload

    def _ship_to_from_acp(self, payload: dict[str, Any]) -> str | None:
        addr = payload.get("fulfillment_address") or {}
        country = addr.get("country")
        state = addr.get("state")
        if country and state:
            return f"{country}-{state}"
        return country

    def acp_create_session(self, payload: dict[str, Any], store_slug: str | None = None,
                           idempotency_key: str | None = None) -> dict[str, Any]:
        if idempotency_key and idempotency_key in self._idempotency:
            existing = self._sessions[self._idempotency[idempotency_key]]
            return self._to_acp(existing)
        store = self._resolve_store(store_slug)
        items = payload.get("line_items") or []
        if not items:
            raise ValueError("line_items must contain at least 1 item")
        first = items[0]
        sess = self._open_session(
            store,
            listing_id=first["id"],
            quantity=int(first.get("quantity", 1)),
            ship_to=self._ship_to_from_acp(payload),
            buyer=payload.get("buyer"),
        )
        if idempotency_key:
            self._idempotency[idempotency_key] = sess.id
        return self._to_acp(sess)

    def acp_get_session(self, session_id: str) -> dict[str, Any] | None:
        sess = self._sessions.get(session_id)
        return self._to_acp(sess) if sess else None

    def acp_update_session(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        sess = self._sessions.get(session_id)
        if sess is None:
            raise KeyError(f"unknown session: {session_id}")
        if sess.status != "awaiting_approval":
            raise ValueError(f"cannot update session in status={sess.status}")
        new_dest = self._ship_to_from_acp(payload) or sess.ship_to
        items = payload.get("line_items")
        qty = int(items[0].get("quantity", sess.quantity)) if items else sess.quantity
        store = self._resolve_store(sess.store_slug)
        pick = self._find_pick(store, sess.listing_id, new_dest)
        sub, ship, tax = _split_cost_lines(pick.cost)
        sess.ship_to = new_dest
        sess.quantity = max(1, qty)
        sess.subtotal_usd = round(sub * sess.quantity, 2)
        sess.shipping_usd = round(ship * sess.quantity, 2)
        sess.tax_duty_usd = round(tax * sess.quantity, 2)
        sess.total_usd = round(sess.subtotal_usd + sess.shipping_usd + sess.tax_duty_usd, 2)
        return self._to_acp(sess)

    def acp_complete_session(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        payment = payload.get("payment_data") or {}
        token = payment.get("token") or payload.get("approval_token")
        sess = self._complete_session(session_id, token)
        return self._to_acp(sess)

    # ------------------------------------------------------------------
    # 4. Human Link-in-bio + JSON-LD + AI Search Discovery Index
    # ------------------------------------------------------------------
    def render_human_page(self, base_url: str, store: StoreProfile) -> str:
        base = base_url.rstrip("/")
        store_url = f"{base}/s/{store.slug}"
        picks = self._catalog(store)
        json_ld = {
            "@context": "https://schema.org",
            "@type": "Store",
            "name": store.name,
            "description": store.bio,
            "url": store_url,
            "sameAs": [store.source_url],
            "hasOfferCatalog": {
                "@type": "OfferCatalog",
                "name": f"{store.name} Catalog",
                "itemListElement": [
                    {
                        "@type": "Product",
                        "sku": p.listing.id,
                        "name": p.listing.title_en,
                        "offers": {
                            "@type": "Offer",
                            "priceCurrency": "USD",
                            "price": f"{p.cost.total_usd:.2f}",
                            "availability": "https://schema.org/InStock",
                            "url": p.listing.url,
                        },
                    }
                    for p in picks
                ],
            },
        }
        cards = "\n".join(
            f"<li><strong>{html.escape(p.listing.title_en)}</strong> "
            f"({html.escape(p.listing.condition)}) — <b>${p.cost.total_usd:.2f} landed</b> "
            f"<code>{html.escape(p.listing.id)}</code></li>"
            for p in picks
        ) or "<li>No listings matched.</li>"

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(store.name)} — Agentic Link-in-Bio</title>
  <link rel="ucp-manifest" href="{store_url}/.well-known/ucp">
  <link rel="mcp-server" href="{store_url}/mcp">
  <link rel="acp-checkout" href="{store_url}/acp/checkout_sessions">
  <script type="application/ld+json">{json.dumps(json_ld, ensure_ascii=False)}</script>
</head>
<body>
  <h1>{html.escape(store.name)}</h1>
  <p>{html.escape(store.bio)}</p>
  <p>Source: <a href="{html.escape(store.source_url)}">{html.escape(store.source_url)}</a></p>
  <h2>Agent Protocol Endpoints (1 URL = Human + MCP + UCP + ACP)</h2>
  <ul>
    <li>MCP (Claude / Cursor / Agents): <code>{store_url}/mcp</code></li>
    <li>UCP (Google / Shopify): <code>{store_url}/.well-known/ucp</code></li>
    <li>ACP (OpenAI / Stripe): <code>{store_url}/acp/checkout_sessions</code></li>
  </ul>
  <h2>Catalog (Landed Cost Verified)</h2>
  <ul>{cards}</ul>
</body>
</html>
"""

    def discovery_index(self, base_url: str) -> dict[str, Any]:
        base = base_url.rstrip("/")
        stores = self.registry.list_all()
        return {
            "protocol_versions": {
                "mcp": MCP_PROTOCOL_VERSION,
                "ucp": UCP_VERSION,
                "acp": ACP_VERSION,
            },
            "global_endpoints": {
                "mcp": f"{base}/mcp",
                "ucp_manifest": f"{base}/.well-known/ucp",
                "acp_checkout_sessions": f"{base}/acp/checkout_sessions",
                "llms_txt": f"{base}/llms.txt",
                "sitemap_xml": f"{base}/sitemap.xml",
            },
            "stores": [
                {
                    "slug": s.slug,
                    "name": s.name,
                    "source_url": s.source_url,
                    "human_url": f"{base}/s/{s.slug}",
                    "mcp_url": f"{base}/s/{s.slug}/mcp",
                    "ucp_manifest_url": f"{base}/s/{s.slug}/.well-known/ucp",
                    "acp_checkout_url": f"{base}/s/{s.slug}/acp/checkout_sessions",
                }
                for s in stores
            ],
        }

    def render_llms_txt(self, base_url: str) -> str:
        base = base_url.rstrip("/")
        lines = [
            "# AWS-SHOPPING Universal Agentic Commerce Gateway",
            "",
            "> 1 URL per creator/shop exposing Human Link-in-Bio + MCP + UCP + ACP with cited landed-cost quotes and human-approval checkout.",
            "",
            "## Global Endpoints",
            f"- Unified Discovery JSON: {base}/.well-known/agent-commerce.json",
            f"- MCP Server (JSON-RPC 2.0): {base}/mcp",
            f"- UCP Manifest: {base}/.well-known/ucp",
            f"- ACP Checkout Sessions: {base}/acp/checkout_sessions",
            "",
            "## Registered Stores",
        ]
        for s in self.registry.list_all():
            lines.append(
                f"- [{s.name}]({base}/s/{s.slug}): source={s.source_url} | "
                f"mcp={base}/s/{s.slug}/mcp | ucp={base}/s/{s.slug}/.well-known/ucp | "
                f"acp={base}/s/{s.slug}/acp/checkout_sessions"
            )
        return "\n".join(lines) + "\n"

    def render_sitemap_xml(self, base_url: str) -> str:
        base = base_url.rstrip("/")
        urls = [f"{base}/", f"{base}/llms.txt", f"{base}/.well-known/ucp"]
        for s in self.registry.list_all():
            urls.append(f"{base}/s/{s.slug}")
        entries = "".join(f"<url><loc>{html.escape(u)}</loc></url>" for u in urls)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{entries}</urlset>"
        )
