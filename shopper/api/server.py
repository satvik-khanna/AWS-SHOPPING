"""HTTP JSON API over the same objects the CLI uses. Stdlib only, CORS for any origin.

    GET  /health
    POST /api/run           {query, budget, ship_to?, user?, condition_min?} -> PipelineResult
    POST /api/approve       {approval_token}                                 -> CartResult
    POST /api/reject        {user?, seller, approval_token?, reason?}        -> Preferences
    GET  /api/memory?user=                                                   -> Preferences
    GET  /api/sessions/<id>                                                  -> Checkout session step log
    POST /api/chat          {user?, message, session?, payment_method?}      -> {reply, components, trail, ...}
    POST /api/buy           {query, budget, ..., listing_id, session?}       -> PipelineResult + cua + components

One Deps lives for the whole server, so approval tokens issued by /api/run are still
valid on a later /api/approve. Approval is decided only by deps.checkout.approve
(unknown or reused token -> status "blocked"); the API adds no other path to "placed".
"""
from __future__ import annotations

import inspect
import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .. import config
from ..app import build_deps
from ..models import CartResult, Listing, ShoppingRequest
from ..pipeline import Deps, run
from . import chat as C
from .sie_rerank import rerank

MAX_BODY = 64 * 1024
CONDITIONS = ("new", "like_new", "good", "fair")
VERSION = "1"
MAX_SESSIONS = 500
SESSION_IDLE_S = 24 * 3600


class _NoCart:
    """Checkout stand-in for listing only: the pipeline would otherwise mint a cart for picks[0]."""

    def fill_cart(self, pick, **_):
        return CartResult(status="blocked", listing_id=pick.listing.id, detail="list only")

    def approve(self, approval_token):
        return CartResult(status="blocked", listing_id="", detail="list only")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


def _bad(message: str) -> ApiError:
    return ApiError(400, "bad_request", message)


def _user(body: dict) -> str:
    u = body.get("user", body.get("user_id"))
    if u is None or u == "":
        return "default"
    if not isinstance(u, str) or len(u) > 80:
        raise _bad("user must be a string of at most 80 characters")
    return u.strip() or "default"


def parse_run(body: dict) -> ShoppingRequest:
    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        raise _bad("query is required (non-empty string)")
    if len(query) > 300:
        raise _bad("query is too long (max 300 characters)")
    budget = body.get("budget")
    if isinstance(budget, bool) or not isinstance(budget, (int, float, str)):
        raise _bad("budget is required (number, USD)")
    try:
        budget = float(budget)
    except ValueError:
        raise _bad("budget must be a number (USD)") from None
    if not math.isfinite(budget) or budget <= 0:
        raise _bad("budget must be greater than 0")
    ship_to = body.get("ship_to") or "US-CA"
    if not isinstance(ship_to, str) or len(ship_to) > 40:
        raise _bad("ship_to must be a string like \"US-CA\"")
    cond = body.get("condition_min") or None
    if cond is not None and cond not in CONDITIONS:
        raise _bad(f"condition_min must be one of {', '.join(CONDITIONS)} or null")
    return ShoppingRequest(query=query.strip(), max_total_usd=budget, ship_to=ship_to.strip(),
                           user_id=_user(body), condition_min=cond)


class Service:
    """Business side of the API, no HTTP. One instance (one Deps) per server."""

    def __init__(self, deps: Deps | None = None):
        self.deps = deps or build_deps()
        self.lock = threading.Lock()
        self.carts: dict[str, tuple[str, Listing]] = {}   # approval_token -> (user, listing)
        self.revoked: set[str] = set()   # carts whose seller the user rejected (Checkout has no cancel())
        self.chats: OrderedDict[str, C.ChatSession] = OrderedDict()
        self.list_deps = Deps(memory=self.deps.memory, source=self.deps.source, cost=self.deps.cost,
                              ranker=self.deps.ranker, checkout=_NoCart())

    def health(self) -> dict:
        d = self.deps
        return {"ok": True, "version": VERSION, "live": config.live(),
                "wiring": {k: type(getattr(d, k)).__name__
                           for k in ("memory", "source", "cost", "ranker", "checkout")}}

    def run(self, body: dict) -> dict:
        req = parse_run(body)
        with self.lock:
            result = run(req, self.deps)
            if result.cart and result.cart.approval_token and result.picks:
                self.carts[result.cart.approval_token] = (req.user_id, result.picks[0].listing)
        out = asdict(result)
        for pick, row in zip(result.picks, out["picks"]):
            row["cost"]["total_usd"] = pick.cost.total_usd   # property, not in asdict
        return out

    def approve(self, body: dict) -> dict:
        token = body.get("approval_token")
        if not isinstance(token, str) or not token:
            raise _bad("approval_token is required (string from /api/run cart.approval_token)")
        with self.lock:
            if token in self.revoked:
                return asdict(CartResult(status="blocked", listing_id="",
                                         detail="cart seller was rejected; run the search again"))
            user, listing = self.carts.get(token, (None, None))
            if listing is not None and listing.seller in self.deps.memory.recall(user).rejected_sellers:
                self.revoked.add(token)
                self.carts.pop(token, None)
                return asdict(CartResult(status="blocked", listing_id=listing.id,
                                         detail="cart seller was rejected; run the search again"))
            cart = self.deps.checkout.approve(token)
            out = asdict(cart)
            if cart.status == "placed":
                user, listing = self.carts.pop(token, (None, None))
                if listing is not None:
                    try:
                        self.deps.memory.remember_purchase(user, listing)
                    except Exception as e:  # order is placed either way; say so
                        out["detail"] = f"{cart.detail}; could not remember purchase: {e}"
        return out

    def reject(self, body: dict) -> dict:
        seller = body.get("seller")
        if not isinstance(seller, str) or not seller.strip():
            raise _bad("seller is required (listing.seller from a pick)")
        if len(seller) > 120:
            raise _bad("seller is too long (max 120 characters)")
        user = _user(body)
        seller_clean = seller.strip()
        token = body.get("approval_token")
        reason = body.get("reason") if isinstance(body.get("reason"), str) else ""
        with self.lock:
            self.deps.memory.reject_seller(user, seller_clean)
            tokens_to_reject: list[str] = []
            if isinstance(token, str) and token:
                tokens_to_reject.append(token)
            for t, (u, l) in list(self.carts.items()):
                if u == user and l.seller == seller_clean and t not in tokens_to_reject:
                    tokens_to_reject.append(t)
            for t in tokens_to_reject:
                self.revoked.add(t)
                self.carts.pop(t, None)
                if hasattr(self.deps.checkout, "reject"):
                    self.deps.checkout.reject(t, reason=reason or "seller rejected", reject_seller=False)
            return asdict(self.deps.memory.recall(user))

    def session(self, session_id: str) -> dict:
        if not session_id or len(session_id) > 64 or "/" in session_id:
            raise _bad("invalid session id")
        fn = getattr(self.deps.checkout, "session", None)
        with self.lock:
            s = fn(session_id) if callable(fn) else None
        if s is None:
            raise ApiError(404, "not_found", f"no checkout session {session_id}")
        return {
            "id": s.id,
            "state": s.state,
            "listing_id": s.listing_id,
            "total_usd": s.total_usd,
            "driver": s.driver,
            "cart_url": s.cart_url,
            "order_ref": s.order_ref,
            "warnings": list(s.warnings),
            "steps": [asdict(st) for st in s.steps],
        }

    # ------------------------------------------------------------ chat + buy (A1)
    def _ranker_name(self) -> str:
        r = self.deps.ranker
        st = getattr(r, "last_status", "")
        return type(r).__name__ + (f" [{st}]" if st else "")

    def _search(self, req: ShoppingRequest) -> tuple[list, list[str], dict]:
        """Pipeline without a cart, then the optional Superlinked SIE rerank. Caller holds the lock."""
        result = run(req, self.list_deps, top_k=C.MAX_ROWS)
        trail = [t for t in result.trail if not t.startswith("cart ")]
        picks, line = rerank(req.query, result.picks, self._ranker_name())
        trail.append(line)
        if picks:
            trail.append("no cart yet: waiting for the user to press Buy on a row")
        return picks, trail, asdict(result.preferences)

    def _revoke(self, token: str | None, reason: str) -> None:
        if not token or token in self.revoked:
            return
        self.revoked.add(token)
        self.carts.pop(token, None)
        if hasattr(self.deps.checkout, "reject"):
            self.deps.checkout.reject(token, reason=reason, reject_seller=False)

    def _fill(self, req: ShoppingRequest, pick) -> CartResult:
        sig = inspect.signature(self.deps.checkout.fill_cart)
        kw = {}
        if "budget_usd" in sig.parameters:
            kw["budget_usd"] = req.max_total_usd
        if "user_id" in sig.parameters:
            kw["user_id"] = req.user_id
        cart = self.deps.checkout.fill_cart(pick, **kw)
        if cart.approval_token:
            self.carts[cart.approval_token] = (req.user_id, pick.listing)
        return cart

    def _cua(self, pick, cart: CartResult, open_real: bool, trail: list[str]) -> dict:
        """Open the real site in Chrome only when SHOPPER_LIVE=1 and the client asked (open_real)."""
        if not config.live():
            return {"opened_in_chrome": False, "skipped": "offline (SHOPPER_LIVE is not 1): no browser opened"}
        if not open_real:
            return {"opened_in_chrome": False, "skipped": "open_real not requested"}
        from ..checkout import don_tacos_cua
        info = don_tacos_cua.probe()
        if not info.get("ok"):
            return {**info, "opened_in_chrome": False}
        try:
            drv = don_tacos_cua.DonTacosChromeDriver()
            sid = cart.session_id or pick.listing.id
            info["opened_url"] = drv.open_listing(sid, asdict(pick.listing))
            info["opened_in_chrome"] = True
            try:
                info.update(drv.inspect_window(sid))
            except Exception:
                pass
            trail.append(f"don-tacos dt-ax opened Chrome: {info['opened_url']}")
        except Exception as e:
            info["error"] = str(e)[:200]
            info["opened_in_chrome"] = False
        return info

    def _buy_pick(self, s: C.ChatSession | None, req: ShoppingRequest, pick, open_real: bool,
                  payment_method: dict | None, trail: list[str]) -> tuple[CartResult, dict, list[dict]]:
        if s is not None:
            self._revoke(s.cart_token, "superseded by a newer cart")
        cart = self._fill(req, pick)
        trail.append(f"cart {cart.status} for {cart.listing_id}"
                     + (" (stopped: waiting for the swipe)" if cart.status == "awaiting_approval" else ""))
        cua = self._cua(pick, cart, open_real, trail)
        fn = getattr(self.deps.checkout, "session", None)
        csession = fn(cart.session_id) if callable(fn) and cart.session_id else None
        comps = [C.agent_browser(pick, cart, csession, cua)]
        if cart.status == "awaiting_approval" and cart.approval_token:
            comps.append(C.swipe_to_pay(pick, cart, csession, req.user_id, payment_method))
        else:
            comps.append(C.notice(f"Could not prepare the cart: {cart.detail}", "warn"))
        comps.append(C.cost_breakdown(pick))
        if s is not None:
            s.cart_token = cart.approval_token if cart.status == "awaiting_approval" else None
            s.payment_method = payment_method or s.payment_method
        return cart, cua, comps

    def _session(self, sid, user: str) -> tuple[C.ChatSession, list[dict]]:
        now = time.time()
        for k in [k for k, v in self.chats.items() if now - v.updated_at > SESSION_IDLE_S]:
            self.chats.pop(k)
        notes = []
        if sid is not None and (not isinstance(sid, str) or len(sid) > 64):
            raise _bad("session must be the string returned by an earlier /api/chat")
        s = self.chats.get(sid) if sid else None
        if s is not None and s.user != user:
            raise ApiError(403, "forbidden", "this chat session belongs to another user")
        if s is None:
            if sid:
                notes.append(C.notice("That chat session has expired; started a new one.", "info"))
            s = C.ChatSession(id=C.new_session_id(), user=user)
            self.chats[s.id] = s
            while len(self.chats) > MAX_SESSIONS:
                self.chats.popitem(last=False)
        s.updated_at = now
        self.chats.move_to_end(s.id)
        return s, notes

    @staticmethod
    def _pm(body: dict):
        try:
            return C.parse_payment_method(body.get("payment_method"))
        except ValueError as e:
            raise _bad(str(e)) from None

    def chat(self, body: dict) -> dict:
        message = body.get("message")
        if not isinstance(message, str):
            raise _bad("message is required (string), e.g. \"list up Fujifilm X100V under $900\"")
        if len(message) > 500:
            raise _bad("message is too long (max 500 characters)")
        user = _user(body)
        pm = self._pm(body)
        intent, args = C.parse_intent(message)
        trail: list[str] = [f"intent: {intent} (rule-based)"]
        with self.lock:
            s, comps = self._session(body.get("session"), user)
            if pm:
                s.payment_method = pm
            reply = self._chat_intent(s, intent, args, body, trail, comps)
        return {"version": C.COMPONENT_VERSION, "session": s.id, "user": user, "intent": intent,
                "reply": reply, "components": comps, "trail": trail}

    def _chat_intent(self, s: C.ChatSession, intent: str, args: dict, body: dict,
                     trail: list[str], comps: list[dict]) -> str:
        examples = ("Try: \"list up Fujifilm X100V under $900\", \"sort by price\", "
                    "\"sort by condition\", \"buy 1\" or \"buy <listing id>\".")
        if intent == "help":
            comps.append(C.notice(examples))
            return "I search Japanese marketplaces and show landed prices. " + examples
        if intent == "search":
            prev = s.request
            merged = {"query": args.get("query"), "user": s.user,
                      "budget": args.get("budget", body.get("budget") or (prev.max_total_usd if prev else C.DEFAULT_BUDGET_USD)),
                      "ship_to": args.get("ship_to") or body.get("ship_to") or (prev.ship_to if prev else "US-CA"),
                      "condition_min": args.get("condition_min") or body.get("condition_min")}
            req = parse_run(merged)
            if "budget" not in args and body.get("budget") is None:
                trail.append(f"budget not in message: using ${req.max_total_usd:g}"
                             + (" (previous search)" if prev else " (default)"))
            self._revoke(s.cart_token, "superseded by a new search")
            s.cart_token = None
            picks, t, _ = self._search(req)
            trail.extend(t)
            s.request, s.picks, s.sort = req, picks, ("relevance", "asc")
            if not picks:
                comps.append(C.notice(f"Nothing for \"{req.query}\" fits ${req.max_total_usd:,.0f} landed. "
                                      "Try a higher budget or other words.", "warn"))
                comps.append(C.listing_table(s))
                return f"No listing for \"{req.query}\" fits ${req.max_total_usd:,.0f} landed to {req.ship_to}."
            comps.append(C.listing_table(s))
            return (f"Found {len(picks)} for \"{req.query}\" within ${req.max_total_usd:,.0f} landed to {req.ship_to}, "
                    "best match first. Press Buy on a row, or say \"sort by price\".")
        if not s.picks:
            comps.append(C.notice("There is no list yet. " + examples, "info"))
            return "Tell me what to look for first. " + examples
        if intent == "list":
            comps.append(C.listing_table(s))
            return f"Here is the list again ({len(s.picks)} items, sorted by {s.sort[0]})."
        if intent == "sort":
            s.sort = (args["key"], args["dir"])
            trail.append(f"sorted {len(s.picks)} rows by {args['key']} {args['dir']}")
            comps.append(C.listing_table(s))
            return f"Sorted by {args['key']} ({'low to high' if args['dir'] == 'asc' else 'high to low'})."
        if intent == "bad_sort":
            comps.append(C.notice(f"I can sort by price, condition or relevance, not \"{args['key']}\".", "warn"))
            return "I can sort by price, condition or relevance."
        if intent == "buy":
            pick = self._resolve(s, args["target"])
            if pick is None:
                comps.append(C.notice(f"\"{args['target']}\" is not in the current list. "
                                      "Use the row number or the listing id.", "warn"))
                comps.append(C.listing_table(s))
                return f"I could not find \"{args['target']}\" in the list."
            cart, _, more = self._buy_pick(s, s.request, pick, bool(body.get("open_real")), s.payment_method, trail)
            comps.extend(more)
            if cart.status != "awaiting_approval":
                return f"I could not prepare the cart for {pick.listing.title_en}: {cart.detail}"
            return (f"Cart ready for {pick.listing.title_en} at ${pick.cost.total_usd:,.2f} landed. "
                    "Nothing is paid yet: swipe to pay to confirm.")
        if intent == "approve":
            comps.append(C.notice("Approval only happens through the swipe-to-pay control (POST /api/approve); "
                                  "typing in chat never pays.", "info"))
            return "Please use the swipe-to-pay control to confirm. Typing cannot pay."
        if intent == "reject":
            self._revoke(s.cart_token, "declined in chat")
            had = s.cart_token is not None
            s.cart_token = None
            comps.append(C.notice("Cart cancelled; nothing was paid." if had else "No pending cart to cancel.", "info"))
            comps.append(C.listing_table(s))
            return "OK, nothing was paid." if had else "There is no pending cart."
        comps.append(C.notice(examples))
        return examples

    @staticmethod
    def _resolve(s: C.ChatSession, target: str):
        if target.isdigit():
            rows = C.sorted_picks(s.picks, *s.sort)
            i = int(target) - 1
            return rows[i] if 0 <= i < len(rows) else None
        t = target.lower()
        for p in s.picks:
            lid = p.listing.id.lower()
            if lid == t or lid.split(":", 1)[-1] == t:
                return p
        return None

    def buy(self, body: dict) -> dict:
        listing_id = body.get("listing_id")
        if not isinstance(listing_id, str) or not listing_id.strip():
            raise _bad("listing_id is required (listing.id of a pick)")
        target = listing_id.strip()
        req = parse_run(body)
        pm = self._pm(body)
        sid = body.get("session")
        trail: list[str] = []
        with self.lock:
            s = None
            if sid:
                s, _ = self._session(sid, req.user_id)
                if not s.picks:
                    s = None     # unknown / expired session: fall back to a fresh search
            if s is not None and any(p.listing.id == target for p in s.picks):
                picks, prefs = s.picks, asdict(self.deps.memory.recall(req.user_id))
                req = s.request
                trail.append(f"buy {target} from chat session {s.id}")
            else:
                picks, t, prefs = self._search(req)
                trail.extend(t)
            pick = next((p for p in picks if p.listing.id == target), None)
            if pick is None:
                raise ApiError(404, "not_found", f"listing {target} is not in the current results for "
                                                 f"\"{req.query}\" within ${req.max_total_usd:g}; search again")
            cart, cua, comps = self._buy_pick(s, req, pick, bool(body.get("open_real")),
                                              pm or (s.payment_method if s else None), trail)
        out = {"request": asdict(req), "preferences": prefs, "picks": [asdict(p) for p in picks],
               "cart": asdict(cart), "trail": trail, "cua": cua, "components": comps}
        for p, row in zip(picks, out["picks"]):
            row["cost"]["total_usd"] = p.cost.total_usd
        return out

    def memory(self, query: dict) -> dict:
        user = _user({"user": (query.get("user") or query.get("user_id") or [""])[0]})
        with self.lock:
            return asdict(self.deps.memory.recall(user))


def make_handler(service: Service, quiet: bool = False):
    gets = {"/health": lambda q: service.health(), "/api/health": lambda q: service.health(),
            "/api/memory": service.memory}
    posts = {"/api/run": service.run, "/api/approve": service.approve, "/api/reject": service.reject,
             "/api/chat": service.chat, "/api/buy": service.buy}

    class Handler(BaseHTTPRequestHandler):
        server_version = "shopper-api/" + VERSION
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if not quiet:
                super().log_message(fmt, *args)

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")

        def _send(self, status: int, obj) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _route(self, table: dict, other: dict):
            path = urlsplit(self.path).path.rstrip("/") or "/"
            if path in table:
                return path, table[path]
            if path in other:
                raise ApiError(405, "method_not_allowed",
                               f"{self.command} not allowed on {path}; use {'POST' if other is posts else 'GET'}")
            raise ApiError(404, "not_found", f"no endpoint {path}; see docs/API.md")

        def _guard(self, fn) -> None:
            try:
                self._send(200, fn())
            except ApiError as e:
                if e.status == 413:          # body left unread: do not reuse this connection
                    self.close_connection = True
                self._send(e.status, e.body())
            except Exception as e:  # never leak a stack trace, still say what broke
                self._send(500, {"error": {"code": "internal", "message": f"{type(e).__name__}: {e}"}})

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            def go():
                path = urlsplit(self.path).path.rstrip("/") or "/"
                if path.startswith("/api/sessions/"):
                    return service.session(path[len("/api/sessions/"):])
                _, fn = self._route(gets, posts)
                return fn(parse_qs(urlsplit(self.path).query))
            self._guard(go)

        def do_POST(self):
            def go():
                _, fn = self._route(posts, gets)
                return fn(self._json())
            self._guard(go)

        def _json(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise _bad("bad Content-Length") from None
            if n > MAX_BODY:
                raise ApiError(413, "too_large", f"body over {MAX_BODY} bytes")
            raw = self.rfile.read(n) if n > 0 else b""
            if not raw.strip():
                raise _bad("JSON body required")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise _bad("body is not valid JSON") from None
            if not isinstance(body, dict):
                raise _bad("body must be a JSON object")
            return body

    return Handler


def make_server(host: str = "127.0.0.1", port: int = 8000, deps: Deps | None = None,
                quiet: bool = False) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(Service(deps), quiet))
    srv.daemon_threads = True
    return srv
