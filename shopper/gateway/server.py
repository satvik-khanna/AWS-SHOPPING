"""Stdlib HTTP server exposing 1 URL -> Human Link-in-Bio + MCP + UCP + ACP + AI Discovery."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from ..app import build_deps
from ..pipeline import Deps
from .models import StoreProfile
from .protocols import ACP_VERSION, MCP_PROTOCOL_VERSION, UCP_VERSION, UnifiedCommerceAdapter
from .store import JsonStoreRegistry

_STORE_PREFIX = re.compile(r"^/s/([a-z0-9-]+)(/.*)?$")
MAX_BODY = 64 * 1024


class _BodyTooLarge(ValueError):
    pass


class _BadJsonBody(ValueError):
    pass


class GatewayHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], adapter: UnifiedCommerceAdapter):
        self.adapter = adapter
        super().__init__(server_address, _GatewayHandler)


class _GatewayHandler(BaseHTTPRequestHandler):
    server: GatewayHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return  # quiet by default for clean tests and CLI output

    def _base_url(self) -> str:
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}"

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise _BadJsonBody("invalid Content-Length") from None
        if length < 0:
            raise _BadJsonBody("invalid Content-Length")
        if length > MAX_BODY:
            raise _BodyTooLarge(f"body exceeds {MAX_BODY} bytes")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _BadJsonBody(f"invalid json: {exc}") from None
        if not isinstance(data, dict):
            raise _BadJsonBody("json body must be an object")
        return data

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, API-Version, UCP-Version, Idempotency-Key")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, code: int, body: dict[str, Any], extra_headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, code: int, body: str, content_type: str,
                   extra_headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _split_store(self, path: str) -> tuple[str | None, str]:
        m = _STORE_PREFIX.match(path)
        if not m:
            return None, path
        return m.group(1), (m.group(2) or "/")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        base = self._base_url()
        adapter = self.server.adapter

        if path == "/llms.txt":
            return self._send_text(200, adapter.render_llms_txt(base), "text/plain; charset=utf-8")
        if path == "/sitemap.xml":
            return self._send_text(200, adapter.render_sitemap_xml(base), "application/xml; charset=utf-8")
        if path == "/.well-known/agent-commerce.json":
            return self._send_json(200, adapter.discovery_index(base))
        if path == "/.well-known/mcp.json":
            return self._send_json(200, {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "transport": "streamable-http",
                "endpoint": f"{base}/mcp",
                "stores_index": f"{base}/.well-known/agent-commerce.json",
            })
        if path == "/":
            return self._send_json(200, adapter.discovery_index(base))

        slug, sub = self._split_store(path)
        if slug and sub == "/":
            store = adapter.registry.get(slug)
            if store is None:
                return self._send_json(404, {"error": f"store not found: {slug}"})
            store_url = f"{base}/s/{slug}"
            link_hdr = (
                f'<{store_url}/.well-known/ucp>; rel="ucp-manifest", '
                f'<{store_url}/mcp>; rel="mcp-server", '
                f'<{store_url}/acp/checkout_sessions>; rel="acp-checkout"'
            )
            return self._send_text(
                200,
                adapter.render_human_page(base, store),
                "text/html; charset=utf-8",
                extra_headers={"Link": link_hdr},
            )

        if sub == "/.well-known/ucp":
            return self._send_json(200, adapter.ucp_manifest(base, store_slug=slug))

        if sub.startswith("/ucp/checkout-sessions/"):
            sid = sub.split("/ucp/checkout-sessions/", 1)[1].strip("/")
            sess = adapter.ucp_get_session(sid)
            if sess is None:
                return self._send_json(404, {"error": f"session not found: {sid}"})
            return self._send_json(200, sess, {"UCP-Version": UCP_VERSION})

        if sub.startswith("/acp/checkout_sessions/"):
            sid = sub.split("/acp/checkout_sessions/", 1)[1].strip("/")
            sess = adapter.acp_get_session(sid)
            if sess is None:
                return self._send_json(404, {"error": f"session not found: {sid}"})
            return self._send_json(200, sess, {"API-Version": ACP_VERSION})

        return self._send_json(404, {"error": f"not found: {path}"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        base = self._base_url()
        adapter = self.server.adapter
        try:
            payload = self._read_json()
        except _BodyTooLarge as exc:
            return self._send_json(413, {"error": str(exc)})
        except _BadJsonBody as exc:
            return self._send_json(400, {"error": str(exc)})

        if path == "/api/stores":
            if not payload.get("name") or not payload.get("source_url"):
                return self._send_json(400, {"error": "name and source_url are required"})
            prof = adapter.registry.register(
                StoreProfile(
                    slug=payload.get("slug", ""),
                    name=payload["name"],
                    source_url=payload["source_url"],
                    bio=payload.get("bio", ""),
                    ship_from=payload.get("ship_from", "JP"),
                    default_ship_to=payload.get("default_ship_to", "US-CA"),
                    query_filter=payload.get("query_filter", ""),
                    tags=payload.get("tags") or [],
                )
            )
            store_url = f"{base}/s/{prof.slug}"
            return self._send_json(201, {
                "slug": prof.slug,
                "name": prof.name,
                "source_url": prof.source_url,
                "human_url": store_url,
                "mcp_url": f"{store_url}/mcp",
                "ucp_manifest_url": f"{store_url}/.well-known/ucp",
                "acp_checkout_url": f"{store_url}/acp/checkout_sessions",
                "discovery_url": f"{base}/.well-known/agent-commerce.json",
                "llms_txt_url": f"{base}/llms.txt",
            })

        slug, sub = self._split_store(path)
        try:
            # MCP
            if sub == "/mcp":
                return self._send_json(200, adapter.handle_mcp(payload, store_slug=slug))

            # UCP
            if sub == "/ucp/checkout-sessions":
                return self._send_json(201, adapter.ucp_create_session(payload, store_slug=slug),
                                       {"UCP-Version": UCP_VERSION})
            if sub.startswith("/ucp/checkout-sessions/") and sub.endswith("/complete"):
                sid = sub[len("/ucp/checkout-sessions/"):-len("/complete")].strip("/")
                return self._send_json(200, adapter.ucp_complete_session(sid, payload),
                                       {"UCP-Version": UCP_VERSION})
            if sub.startswith("/ucp/checkout-sessions/") and sub.endswith("/cancel"):
                sid = sub[len("/ucp/checkout-sessions/"):-len("/cancel")].strip("/")
                return self._send_json(200, adapter.ucp_cancel_session(sid),
                                       {"UCP-Version": UCP_VERSION})

            # ACP
            if sub == "/acp/checkout_sessions":
                idem = self.headers.get("Idempotency-Key")
                return self._send_json(201, adapter.acp_create_session(payload, store_slug=slug,
                                                                       idempotency_key=idem),
                                       {"API-Version": ACP_VERSION})
            if sub.startswith("/acp/checkout_sessions/") and sub.endswith("/complete"):
                sid = sub[len("/acp/checkout_sessions/"):-len("/complete")].strip("/")
                return self._send_json(200, adapter.acp_complete_session(sid, payload),
                                       {"API-Version": ACP_VERSION})
            if sub.startswith("/acp/checkout_sessions/"):
                sid = sub[len("/acp/checkout_sessions/"):].strip("/")
                return self._send_json(200, adapter.acp_update_session(sid, payload),
                                       {"API-Version": ACP_VERSION})
        except KeyError as exc:
            return self._send_json(404, {"error": str(exc)})
        except PermissionError as exc:
            return self._send_json(403, {"error": str(exc)})
        except ValueError as exc:
            return self._send_json(400, {"error": str(exc)})

        return self._send_json(404, {"error": f"not found: {path}"})


def make_server(host: str = "127.0.0.1", port: int = 8080,
                deps: Deps | None = None,
                registry: JsonStoreRegistry | None = None) -> GatewayHTTPServer:
    adapter = UnifiedCommerceAdapter(deps or build_deps(), registry or JsonStoreRegistry())
    return GatewayHTTPServer((host, port), adapter)
