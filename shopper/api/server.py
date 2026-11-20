"""HTTP JSON API over the same objects the CLI uses. Stdlib only, CORS for any origin.

    GET  /health
    POST /api/run      {query, budget, ship_to?, user?, condition_min?} -> PipelineResult
    POST /api/approve  {approval_token}                                 -> CartResult
    POST /api/reject   {user?, seller}                                  -> Preferences
    GET  /api/memory?user=                                              -> Preferences

One Deps lives for the whole server, so approval tokens issued by /api/run are still
valid on a later /api/approve. Approval is decided only by deps.checkout.approve
(unknown or reused token -> status "blocked"); the API adds no other path to "placed".
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .. import config
from ..app import build_deps
from ..models import CartResult, Listing, ShoppingRequest
from ..pipeline import Deps, run

MAX_BODY = 64 * 1024
CONDITIONS = ("new", "like_new", "good", "fair")
VERSION = "1"


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
        with self.lock:
            self.deps.memory.reject_seller(user, seller.strip())
            return asdict(self.deps.memory.recall(user))

    def memory(self, query: dict) -> dict:
        user = _user({"user": (query.get("user") or query.get("user_id") or [""])[0]})
        with self.lock:
            return asdict(self.deps.memory.recall(user))


def make_handler(service: Service, quiet: bool = False):
    gets = {"/health": lambda q: service.health(), "/api/health": lambda q: service.health(),
            "/api/memory": service.memory}
    posts = {"/api/run": service.run, "/api/approve": service.approve, "/api/reject": service.reject}

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
