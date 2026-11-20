"""python3 -m shopper "used Fujifilm X100V" --budget 900 --ship-to US-CA [--json]"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from . import cli_checkout  # noqa: F401  (routes buy/approve/reject/sessions/log/memory/firedrill)
from .app import build_deps
from .models import ShoppingRequest
from .pipeline import run


def main() -> None:
    p = argparse.ArgumentParser(prog="shopper")
    p.add_argument("query")
    p.add_argument("--budget", type=float, required=True, help="max landed cost in USD")
    p.add_argument("--ship-to", default="US-CA")
    p.add_argument("--user", default="default")
    p.add_argument("--condition-min")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    req = ShoppingRequest(a.query, a.budget, a.ship_to, a.user, a.condition_min)
    r = run(req, build_deps())
    if a.json:
        print(json.dumps(asdict(r), ensure_ascii=False, indent=2))
        return
    for t in r.trail:
        print("-", t)
    for i, pk in enumerate(r.picks, 1):
        print(f"\n#{i} {pk.listing.title_en} ({pk.listing.title_ja}) {pk.listing.url}")
        for line in pk.cost.lines:
            print(f"   {line.label:<24} ${line.amount_usd:>8.2f}  [{line.citation.source} as of {line.citation.as_of}]")
        print(f"   {'total':<24} ${pk.cost.total_usd:>8.2f}")
    if r.cart:
        print(f"\ncart: {r.cart.status} ({r.cart.detail}); approve token: {r.cart.approval_token}")


if __name__ == "__main__":
    main()
