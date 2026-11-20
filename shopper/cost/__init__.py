"""Landed cost (Japan -> ship_to via Buyee) with a citation on every line.

Rules come from fixtures/rules.json (researched, each with as_of + url). With SHOPPER_LIVE=1 and
SENSO_API_KEY set, the same rules document is read from Senso instead (see senso.py); any failure
falls back to the local file.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..config import FIXTURES, live
from ..models import Citation, CostLine, LandedCost, Listing, ShoppingRequest

RULES_PATH = FIXTURES / "rules.json"
SELLER_PAID = {"seller_paid", "included", "送料込み", "出品者負担", "free"}


def load_rules(path: Path = RULES_PATH) -> tuple[dict, str]:
    """Return (rules, source prefix). Senso when live + key, else the local file."""
    if live():
        from .senso import fetch_rules
        got = fetch_rules()
        if got is not None:
            return got, "senso:japan-landed-cost-rules"
    return json.loads(path.read_text(encoding="utf-8")), "fixtures/rules.json"


def _band(bands: list, kg: float) -> int | None:
    for max_kg, jpy in bands:
        if kg <= max_kg + 1e-9:
            return jpy
    return None


class RulesCostEngine:
    def __init__(self, path: Path = RULES_PATH, policy: str | None = None, plan: str | None = None,
                 rules: dict | None = None):
        if rules is not None:
            self.rules, self.prefix = rules, "fixtures/rules.json"
        else:
            self.rules, self.prefix = load_rules(path)
        self.policy = policy or self.rules["shipping_method_policy"]["value"]
        self.plan = plan or self.rules["buyee_plan_fee_jpy"]["default"]

    # --- citations -------------------------------------------------------------------------
    def _cite(self, key: str, sub: str | None = None, url: str | None = None) -> Citation:
        r = self.rules[key]
        src = f"{self.prefix}#{key}" + (f".{sub}" if sub else "")
        if r.get("status") == "estimate":
            src += " (estimate)"
        return Citation(source=src, as_of=r["as_of"], url=url or r.get("url"))

    # --- pieces ----------------------------------------------------------------------------
    def blocked_reason(self, listing: Listing) -> str | None:
        R = self.rules
        cat = (listing.category or "").lower()
        p = R["prohibited"]["value"].get(cat)
        if p:
            return f"banned: {cat}: {p['reason']} ({p['url']})"
        n = listing.fields.get("lithium_batteries")
        if n and str(n).isdigit() and int(n) > R["lithium_installed_max"]["value"]:
            return (f"banned: {n} installed lithium batteries > max {R['lithium_installed_max']['value']} "
                    f"per package ({R['lithium_installed_max']['url']})")
        return None

    def ship_weight(self, listing: Listing) -> float:
        R = self.rules
        return (listing.weight_kg or R["default_weight_kg"]["value"]) + R["packaging_kg"]["value"]

    def choose_method(self, kg: float, category: str | None = None) -> tuple[str, dict, int] | None:
        """(method id, method rule, JPY) for the cheapest allowed method under the policy."""
        methods = self.rules["intl_shipping_us"]["value"]
        opts = []
        for mid, m in methods.items():
            if kg > m["max_kg"] or (category and category in m.get("forbids", [])):
                continue
            if self.policy == "cheapest_tracked" and not m["tracked"]:
                continue
            jpy = _band(m["bands"], kg)
            if jpy is not None:
                opts.append((mid, m, jpy))
        if not opts:
            return None
        if self.policy == "fastest":
            return min(opts, key=lambda o: (int(o[1]["days"].split("-")[0]), o[2]))
        return min(opts, key=lambda o: o[2])

    def domestic_jpy(self, listing: Listing, kg: float) -> tuple[int, str]:
        if str(listing.fields.get("shipping", "")).lower() in SELLER_PAID:
            return 0, "domestic_shipping_seller_paid"
        jpy = _band([[b["max_kg"], b["jpy"]] for b in self.rules["domestic_shipping_jpy"]["value"]], kg)
        return (jpy if jpy is not None else self.rules["domestic_shipping_jpy"]["value"][-1]["jpy"]), \
            "domestic_shipping_jpy"

    def duty(self, listing: Listing, req: ShoppingRequest, item_usd: float) -> list[CostLine]:
        R = self.rules
        country = (req.ship_to or "US").split("-")[0].upper()
        if country != "US":
            return [CostLine(f"duty ({country}: not modeled, $0 shown)", 0.0, self._cite("us_duty_mfn_by_category"))]
        table = R["us_duty_mfn_by_category"]["value"]
        cat = (listing.category or "").lower()
        key = cat if cat in table else "default"
        d = table[key]
        dm = R["us_de_minimis"]["value"]
        note = "de minimis suspended" if dm["suspended"] else ""
        if not dm["suspended"] and item_usd <= dm["threshold_usd"]:
            return [CostLine(f"duty (under ${dm['threshold_usd']} de minimis)", 0.0, self._cite("us_de_minimis"))]
        if "per_unit_usd" in d:
            ad_val = item_usd * d["approx_rate"]
            mfn = d["per_unit_usd"] + ad_val
        else:
            ad_val = mfn = item_usd * d["rate"]
        lines = [CostLine(f"US duty MFN {key} HTS {d['hts'] or '-'} ({note})", round(mfn, 2),
                          self._cite("us_duty_mfn_by_category", key))]
        floor = R["us_japan_floor_rate"]
        if cat not in floor["exempt_categories"]:
            top_up = max(0.0, item_usd * floor["value"] - ad_val)
            if top_up > 0:
                lines.append(CostLine(f"US Japan {floor['value']:.1%} floor top-up (HTS 9903.05.49)",
                                      round(top_up, 2), self._cite("us_japan_floor_rate")))
        return lines

    # --- main ------------------------------------------------------------------------------
    def landed_cost(self, listing: Listing, req: ShoppingRequest) -> LandedCost:
        R = self.rules
        fx = R["fx_jpy_per_usd"]["value"]
        blocked = self.blocked_reason(listing)
        if blocked:
            return LandedCost(listing.id, [], fx, blocked_reason=blocked)
        usd = lambda jpy: round(jpy / fx, 2)  # noqa: E731
        kg = self.ship_weight(listing)
        method = self.choose_method(kg, (listing.category or "").lower())
        if method is None:
            return LandedCost(listing.id, [], fx, blocked_reason=(
                f"cannot ship: no {self.policy} method for {kg:.2f} kg ({R['intl_shipping_us']['url']})"))
        mid, m, ship_jpy = method
        item = usd(listing.price_jpy)
        dom_jpy, dom_key = self.domestic_jpy(listing, kg)
        lines = [
            CostLine(f"item price (JPY {listing.price_jpy:,} @ {fx})", item, self._cite("fx_jpy_per_usd")),
            CostLine("Buyee service fee", usd(R["buyee_service_fee_jpy"]["value"]), self._cite("buyee_service_fee_jpy")),
            CostLine(f"Buyee {self.plan} plan", usd(R["buyee_plan_fee_jpy"]["value"][self.plan]),
                     self._cite("buyee_plan_fee_jpy", self.plan)),
            CostLine("payment fee", usd(R["payment_fee_jpy"]["value"]), self._cite("payment_fee_jpy")),
            CostLine("domestic shipping (JP)", usd(dom_jpy), self._cite(dom_key)),
            CostLine(f"international shipping {m['label']} {kg:.2f} kg ({m['days']} days)", usd(ship_jpy),
                     self._cite("intl_shipping_us", mid, url=m["url"])),
        ]
        cc = R["customs_clearance_fee_jpy"]
        if listing.price_jpy > cc["threshold_jpy"] and mid in cc["methods"]:
            lines.append(CostLine("customs clearance commission (>JPY 200,000)", usd(cc["value"]),
                                  self._cite("customs_clearance_fee_jpy")))
        lines += self.duty(listing, req, item)
        return LandedCost(listing.id, lines, fx)


__all__ = ["RulesCostEngine", "load_rules"]
