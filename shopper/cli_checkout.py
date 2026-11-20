"""Checkout + memory subcommands.

    python3 -m shopper buy "Fujifilm X100V" --budget 900     # search -> cart -> STOP, prints step log + token
    python3 -m shopper approve <token>                        # the only way an order is placed
    python3 -m shopper reject <token> [--reason "scratches"] [--seller]
    python3 -m shopper sessions                               # list carts and their states
    python3 -m shopper log <session_id>                       # step log for the demo
    python3 -m shopper memory [--user u] [--condition-min good] [--size shoes=27cm] [--note ...]
    python3 -m shopper firedrill [-n 130]

(Also `python3 -m shopper.cli_checkout <same args>`.) Plain `python3 -m shopper "<query>"` is untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

SUBCOMMANDS = {"buy", "approve", "reject", "sessions", "log", "memory", "firedrill"}


def _checkout():
    from .checkout import DryRunCheckout
    return DryRunCheckout()


def _print_session(co, detail: str) -> None:
    sid = detail.split("]")[0].split()[-1] if detail.startswith("[session ") else None
    s = co.session(sid) if sid else None
    if s:
        print(f"\nstep log (session {s.id}, driver {s.driver}):")
        for line in s.transcript():
            print("  " + line)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="shopper")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("buy")
    b.add_argument("query"); b.add_argument("--budget", type=float, required=True)
    b.add_argument("--ship-to", default="US-CA"); b.add_argument("--user", default="default")
    b.add_argument("--condition-min"); b.add_argument("--json", action="store_true")
    a = sub.add_parser("approve"); a.add_argument("token")
    r = sub.add_parser("reject"); r.add_argument("token"); r.add_argument("--reason", default="")
    r.add_argument("--seller", action="store_true", help="also never show this seller again")
    sub.add_parser("sessions")
    lg = sub.add_parser("log"); lg.add_argument("session_id")
    m = sub.add_parser("memory"); m.add_argument("--user", default="default")
    m.add_argument("--condition-min"); m.add_argument("--size", action="append", default=[])
    m.add_argument("--note"); m.add_argument("--reject-seller")
    f = sub.add_parser("firedrill"); f.add_argument("-n", type=int, default=130)
    args = p.parse_args(argv)

    if args.cmd == "buy":
        from .app import build_deps
        from .models import ShoppingRequest
        from .pipeline import run
        deps = build_deps()
        co = deps.checkout
        if hasattr(co, "budget_usd"):
            co.budget_usd = args.budget
            co.user_id = args.user
        mem = deps.memory
        prefs = mem.recall(args.user)
        req = ShoppingRequest(args.query, args.budget, args.ship_to, args.user,
                              args.condition_min or prefs.condition_min)
        res = run(req, deps)
        if args.json:
            print(json.dumps(asdict(res), ensure_ascii=False, indent=2)); return 0
        for t in res.trail:
            print("-", t)
        if res.cart:
            print(f"\ncart: {res.cart.status} — {res.cart.detail}")
            if hasattr(co, "session"):
                _print_session(co, res.cart.detail)
            if res.cart.approval_token:
                print(f"\nNothing has been paid. To place: python3 -m shopper approve {res.cart.approval_token}")
                print(f"To refuse:      python3 -m shopper reject {res.cart.approval_token} --reason '...'")
        return 0

    if args.cmd in ("approve", "reject"):
        co = _checkout()
        res = co.approve(args.token) if args.cmd == "approve" else co.reject(args.token, args.reason, args.seller)
        print(f"{args.cmd}: {res.status} — {res.detail}")
        _print_session(co, res.detail)
        return 0 if res.status in ("placed", "blocked") and args.cmd == "reject" or res.status == "placed" else 1

    if args.cmd == "sessions":
        for s in _checkout().store.list():
            print(f"{s.id}  {s.state:<17} ${s.total_usd:>8.2f}  {s.listing_id}  {len(s.warnings)} warning(s)")
        return 0

    if args.cmd == "log":
        s = _checkout().session(args.session_id)
        if not s:
            print("no such session"); return 1
        print("\n".join(s.transcript())); return 0

    if args.cmd == "memory":
        from .memory import JsonMemory
        mem = JsonMemory()
        if args.condition_min:
            mem.set_condition_min(args.user, args.condition_min)
        for kv in args.size:
            k, _, v = kv.partition("=")
            mem.set_size(args.user, k, v)
        if args.note:
            mem.add_note(args.user, args.note)
        if args.reject_seller:
            mem.reject_seller(args.user, args.reject_seller)
        print(json.dumps(asdict(mem.recall(args.user)), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "firedrill":
        sys.path.insert(0, str(__import__("shopper.config", fromlist=["ROOT"]).ROOT))
        from firedrill.run import main as fd
        return fd(["-n", str(args.n)])
    return 2


def _intercept() -> None:
    """Called on import from shopper/__main__.py: route subcommands here, leave the rest alone."""
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS and sys.argv[0].endswith("__main__.py"):
        sys.exit(main(sys.argv[1:]))


_intercept()

if __name__ == "__main__":
    sys.exit(main())
