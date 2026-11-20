"""Demo screen server. Stdlib only, no build step.

    python3 -m web                # http://127.0.0.1:8642
    python3 web/server.py --port 9000

One page (web/static) + a small JSON API over the same objects the CLI uses:

    POST /api/run      {query, budget, ship_to, user_id?, condition_min?}
                       -> same shape as `python3 -m shopper ... --json`,
                          plus picks[].cost.total_usd and a `meta` block
    POST /api/approve  {token}          -> {cart, trail}   (checkout.approve; the only path to "placed")
    POST /api/reject   {seller, ...run} -> run result after memory.reject_seller
    GET  /api/meta                      -> {live, wiring}

The server never pays and adds no checkout logic of its own: approval is decided by
deps.checkout. The one thing it adds is refusing tokens of carts the user has already
moved past (new search / rejected seller), because Checkout has no cancel().
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:           # so `python3 web/server.py` works too
    sys.path.insert(0, str(ROOT))

from shopper import config  # noqa: E402
from shopper.app import build_deps  # noqa: E402
from shopper.checkout import don_tacos_cua  # noqa: E402
from shopper.models import CartResult, Listing, ShoppingRequest  # noqa: E402
from shopper.pipeline import Deps, run  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8642
MAX_BODY = 64 * 1024
CONDITIONS = ("new", "like_new", "good", "fair")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
CSP = "default-src 'self'; img-src 'self' https: data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class BadRequest(ValueError):
    pass


def parse_request(body: dict) -> ShoppingRequest:
    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        raise BadRequest("query is required")
    if len(query) > 300:
        raise BadRequest("query is too long (max 300 characters)")
    try:
        budget = float(body.get("budget"))
    except (TypeError, ValueError):
        raise BadRequest("budget must be a number (USD)") from None
    if not math.isfinite(budget) or budget <= 0:
        raise BadRequest("budget must be greater than 0")
    ship_to = str(body.get("ship_to") or "US-CA").strip()[:40]
    user_id = str(body.get("user_id") or "default").strip()[:80]
    cond = body.get("condition_min") or None
    if cond is not None and cond not in CONDITIONS:
        raise BadRequest(f"condition_min must be one of {', '.join(CONDITIONS)}")
    return ShoppingRequest(query=query.strip(), max_total_usd=budget, ship_to=ship_to,
                           user_id=user_id, condition_min=cond)


class Session:
    """Holds the wired deps for the life of the server (approval tokens live in deps.checkout)."""

    def __init__(self, deps: Deps | None = None):
        self.deps = deps or build_deps()
        self.lock = threading.Lock()
        self.active_token: str | None = None            # token of the cart currently on screen
        self.revoked: set[str] = set()                  # tokens of carts the user moved past
        self.in_cart: dict[str, tuple[str, Listing]] = {}   # token -> (user_id, listing)
        self._last_picks: list = []                     # RankedPick objects from last _run

    def meta(self) -> dict:
        d = self.deps
        return {"live": config.live(),
                "cua": don_tacos_cua.probe(),
                "wiring": {k: type(getattr(d, k)).__name__ for k in ("memory", "source", "cost", "ranker", "checkout")}}

    def _revoke_active(self, reason: str = "superseded") -> None:
        if self.active_token:
            tok = self.active_token
            self.revoked.add(tok)
            self.in_cart.pop(tok, None)
            self.active_token = None
            if hasattr(self.deps.checkout, "reject"):
                self.deps.checkout.reject(tok, reason=reason, reject_seller=False)

    def _run(self, req: ShoppingRequest, lead: list[str] | None = None) -> dict:
        self._revoke_active("new search")               # a new cart replaces the one on screen
        result = run(req, self.deps)
        self._last_picks = list(result.picks)
        out = asdict(result)
        for pick, row in zip(result.picks, out["picks"]):
            row["cost"]["total_usd"] = pick.cost.total_usd
        if lead:
            out["trail"] = lead + out["trail"]
        if result.cart and result.cart.approval_token and result.picks:
            self.active_token = result.cart.approval_token
            self.in_cart[self.active_token] = (req.user_id, result.picks[0].listing)
        out["meta"] = self.meta()
        return out

    def run(self, body: dict) -> dict:
        req = parse_request(body)
        with self.lock:
            return self._run(req)

    def reject(self, body: dict) -> dict:
        seller = body.get("seller")
        if not isinstance(seller, str) or not seller.strip():
            raise BadRequest("seller is required")
        seller = seller.strip()
        req = parse_request(body)
        with self.lock:
            self.deps.memory.reject_seller(req.user_id, seller)
            return self._run(req, lead=[f"rejected seller {seller}: remembered for {req.user_id}, searching again"])

    def buy(self, body: dict) -> dict:
        listing_id = body.get("listing_id")
        if not isinstance(listing_id, str) or not listing_id.strip():
            raise BadRequest("listing_id is required")
        target_id = listing_id.strip()
        req = parse_request(body)
        with self.lock:
            out = self._run(req)
            target_pick = next((p for p in self._last_picks if p.listing.id == target_id), None)
            if target_pick is not None and (not out.get("cart") or out["cart"].get("listing_id") != target_id):
                self._revoke_active(f"switched to {target_id}")
                sig = inspect.signature(self.deps.checkout.fill_cart)
                kw = {}
                if "budget_usd" in sig.parameters:
                    kw["budget_usd"] = req.max_total_usd
                if "user_id" in sig.parameters:
                    kw["user_id"] = req.user_id
                cart = self.deps.checkout.fill_cart(target_pick, **kw)
                out["cart"] = asdict(cart)
                if cart.approval_token:
                    self.active_token = cart.approval_token
                    self.in_cart[cart.approval_token] = (req.user_id, target_pick.listing)
                l_data = asdict(target_pick.listing)
                cua_info = don_tacos_cua.probe()
                if config.live() and cua_info.get("ok"):
                    try:
                        drv = don_tacos_cua.DonTacosChromeDriver()
                        opened_url = drv.open_listing(cart.approval_token or target_pick.listing.id, l_data)
                        cua_info["opened_url"] = opened_url
                        out["trail"].append(f"don-tacos dt-ax opened Chrome tab: {opened_url}")
                    except Exception as e:
                        cua_info["error"] = str(e)
                out["cua"] = cua_info
                out["trail"].append(f"cart prepared for {target_pick.listing.id} (awaiting swipe approval)")
            return out

    def approve(self, body: dict) -> dict:
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise BadRequest("token is required")
        with self.lock:
            if token in self.revoked:
                cart = CartResult(status="blocked", listing_id="",
                                  detail="approval token belongs to a cart you already moved past")
                return {"cart": asdict(cart), "trail": [f"approval refused: {cart.detail}"]}
            cart = self.deps.checkout.approve(token)
            trail = [f"approval {'accepted' if cart.status == 'placed' else 'refused'}: "
                     f"cart {cart.status}" + (f" for {cart.listing_id}" if cart.listing_id else "")
                     + (f" ({cart.detail})" if cart.detail else "")]
            if cart.status == "placed":
                user_id, listing = self.in_cart.pop(token, (None, None))
                if token == self.active_token:
                    self.active_token = None
                if listing is not None:
                    try:
                        self.deps.memory.remember_purchase(user_id, listing)
                        trail.append(f"remembered purchase {listing.id} for {user_id}")
                    except Exception as e:          # the order is placed either way; say so, do not hide it
                        trail.append(f"could not remember purchase {listing.id}: {e}")
            return {"cart": asdict(cart), "trail": trail}


def make_handler(session: Session, allowed_hosts: tuple[str, ...] | None, quiet: bool = False):
    static = {f"/static/{p.relative_to(STATIC).as_posix()}": p for p in STATIC.rglob("*") if p.is_file() and p.suffix in MIME}
    static["/"] = static["/index.html"] = STATIC / "index.html"
    posts = {"/api/run": session.run, "/api/buy": session.buy, "/api/approve": session.approve, "/api/reject": session.reject}

    class Handler(BaseHTTPRequestHandler):
        server_version = "shopper-web"

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            """Loopback bind: only answer to loopback names, so another site cannot reach the approve API by DNS rebinding."""
            if allowed_hosts is None:
                return True
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            if host in allowed_hosts:
                return True
            self._json(403, {"error": "unexpected Host header"})
            return False

        def do_GET(self) -> None:
            if not self._host_ok():
                return
            path = self.path.split("?", 1)[0]
            if path == "/api/meta":
                return self._json(200, session.meta())
            if path in posts:
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            f = static.get(path)
            if f is None:
                return self._json(404, {"error": "not found"})
            self._send(200, f.read_bytes(), MIME[f.suffix])

        do_HEAD = do_GET

        def do_POST(self) -> None:
            if not self._host_ok():
                return
            fn = posts.get(self.path.split("?", 1)[0])
            if fn is None:
                return self._json(404, {"error": "not found"})
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._json(415, {"error": "Content-Type must be application/json"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._json(413, {"error": "request body too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise BadRequest("body must be a JSON object")
                out = fn(body)
            except (BadRequest, json.JSONDecodeError, UnicodeDecodeError) as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:      # a module failed (live adapter down, bad fixture): show it, do not hang the page
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
            self._json(200, out)

        def log_message(self, fmt: str, *args) -> None:
            if not quiet:
                sys.stderr.write("web: " + (fmt % args) + "\n")

    return Handler


def create_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT, deps: Deps | None = None,
                  quiet: bool = False) -> ThreadingHTTPServer:
    session = Session(deps)
    allowed = LOOPBACK if host in LOOPBACK else None
    httpd = ThreadingHTTPServer((host, port), make_handler(session, allowed, quiet))
    httpd.daemon_threads = True
    httpd.session = session     # for tests
    return httpd


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="web", description="Japan shopping agent demo screen")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: this machine only)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = p.parse_args(argv)
    try:
        httpd = create_server(a.host, a.port)
    except OSError as e:
        sys.exit(f"cannot listen on {a.host}:{a.port} ({e.strerror}). Try another port: python3 -m web --port {a.port + 1}")
    host, port = httpd.server_address[:2]
    mode = "LIVE adapters enabled (SHOPPER_LIVE=1)" if config.live() else "offline (no live or paid calls)"
    print(f"Japan shopping agent demo: http://{'localhost' if host in LOOPBACK else host}:{port}/   [{mode}]")
    print("Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
