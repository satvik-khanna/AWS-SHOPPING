"""Landed cost with a citation on every line. Base: rules from fixtures/rules.json.
TODO(cost pane): researched fee / shipping / duty rules with real as_of + URL, Senso adapter behind SHOPPER_LIVE."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import FIXTURES
from ..models import Citation, CostLine, LandedCost, Listing, ShoppingRequest


class RulesCostEngine:
    def __init__(self, path: Path = FIXTURES / "rules.json"):
        self.rules = json.loads(path.read_text(encoding="utf-8"))

    def _cite(self, key: str) -> Citation:
        r = self.rules[key]
        return Citation(source=f"fixtures/rules.json#{key}", as_of=r["as_of"], url=r.get("url"))

    def landed_cost(self, listing: Listing, req: ShoppingRequest) -> LandedCost:
        R = self.rules
        fx = R["fx_jpy_per_usd"]["value"]
        if listing.category in R["banned_categories"]["value"]:
            return LandedCost(listing.id, [], fx, blocked_reason=f"banned category: {listing.category}")
        usd = lambda jpy: round(jpy / fx, 2)
        item = usd(listing.price_jpy)
        weight = listing.weight_kg or R["default_weight_kg"]["value"]
        lines = [
            CostLine("item price", item, self._cite("fx_jpy_per_usd")),
            CostLine("proxy service fee", usd(R["proxy_service_fee_jpy"]["value"]), self._cite("proxy_service_fee_jpy")),
            CostLine("domestic shipping", usd(R["domestic_shipping_jpy"]["value"]), self._cite("domestic_shipping_jpy")),
            CostLine("international shipping",
                     usd(R["intl_shipping_jpy_per_kg"]["value"] * max(weight, 0.5)), self._cite("intl_shipping_jpy_per_kg")),
        ]
        duty_rate = R["duty_rate_by_category"]["value"].get(listing.category or "", R["duty_rate_by_category"]["value"]["default"])
        lines.append(CostLine("duty", round(item * duty_rate, 2), self._cite("duty_rate_by_category")))
        return LandedCost(listing.id, lines, fx)
