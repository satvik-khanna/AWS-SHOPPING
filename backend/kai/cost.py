"""Conservative landed-cost estimate with citations.

Every line says where its number came from:
  observed    read from the listing or post
  sourced     a published rate from a cited document
  assumption  KAI's configurable guess, with no source behind the number

The estimate deliberately errs high (proxy path, EMS shipping, top-of-range
duty, FX buffer). It is not a quote and not tax advice."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from .config import PKG, Settings
from .integrations.senso import SensoAdapter
from .models import Candidate, Citation, CostLine, LandedCost, now_iso
from .observability import ActivityLog
from .store import Store

_COMPONENT_QUESTIONS = {
    "duty": ("What import duty applies to low-value goods mailed from Japan to the United States now that the "
             "de minimis exemption is suspended?", "dutySourceIds"),
    "clearance": ("What customs clearance or prepaid duty processing fee applies to DDP postal shipments from "
                  "Japan to the United States?", "clearanceSourceIds"),
    "shipping": ("What is the Japan Post EMS rate to the United States by weight?", "shippingSourceIds"),
    "use_tax": ("Does California use tax apply to goods purchased from a foreign seller?", "useTaxSourceIds"),
}


def load_cost_parameters() -> dict[str, Any]:
    return json.loads((PKG / "data" / "cost_sources.json").read_text(encoding="utf-8"))["parameters"]


class FxService:
    """JPY->USD rate. Live from the Frankfurter API (ECB reference rates) when
    reachable, otherwise the configured fallback, labeled as such."""

    URL = "https://api.frankfurter.dev/v1/latest"

    def __init__(self, settings: Settings, store: Store, log: ActivityLog,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.store = store
        self.log = log
        self._transport = transport
        self.last_error: str | None = None

    async def usd_per_jpy(self, run_id: str | None = None) -> tuple[float, str, str | None]:
        cached = self.store.cache_get("fx:JPYUSD")
        if cached:
            data, _ = cached
            return data["rate"], data["source"] + " (cached)", data["asOf"]
        if self.settings.fx_live:
            try:
                async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(8.0, connect=4.0)) as c:
                    r = await c.get(self.URL, params={"base": "JPY", "symbols": "USD"})
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}")
                body = r.json()
                rate = float(body["rates"]["USD"])
                if not 0.001 < rate < 0.05:
                    raise RuntimeError(f"implausible rate {rate}")
                data = {"rate": rate, "source": "Frankfurter API (ECB reference rate), live", "asOf": body.get("date")}
                self.store.cache_set("fx:JPYUSD", data, 6 * 3600)
                self.last_error = None
                self.log.log("fx", f"Live FX rate: 1 JPY = {rate:.6f} USD (as of {body.get('date')})", "ok", run_id)
                return rate, data["source"], data["asOf"]
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                self.log.log("fx", f"Live FX lookup failed ({self.last_error}); using configured fallback rate.", "warn", run_id)
        return (self.settings.fx_fallback_usd_per_jpy,
                "Configured fallback rate (FX_FALLBACK_USD_PER_JPY) - not a live rate", None)


_US_HINTS = re.compile(r"\b(US|USA|U\.S\.A?\.?|United States|America)\b|California", re.I)


def _is_us_destination(destination: str) -> bool:
    return bool(_US_HINTS.search(destination or ""))


class LandedCostEstimator:
    def __init__(self, settings: Settings, senso: SensoAdapter, fx: FxService, log: ActivityLog):
        self.settings = settings
        self.senso = senso
        self.fx = fx
        self.log = log
        self.params = load_cost_parameters()
        self._cite_cache: tuple[float, dict[str, list[Citation]], str] | None = None

    async def _citations(self, run_id: str | None) -> tuple[dict[str, list[Citation]], str]:
        """One retrieval per cost component, reused for every candidate in a run."""
        if self._cite_cache and self._cite_cache[0] > time.time():
            return self._cite_cache[1], self._cite_cache[2]
        out: dict[str, list[Citation]] = {}
        backends: set[str] = set()
        for key, (question, param_key) in _COMPONENT_QUESTIONS.items():
            cites, backend = await self.senso.cite(question, self.params[param_key], run_id)
            out[key] = cites
            backends.add(backend)
        backend = "senso" if backends == {"senso"} else ("bundled" if backends == {"bundled"} else "senso+bundled")
        self._cite_cache = (time.time() + 600, out, backend)
        n = len({c.id for v in out.values() for c in v})
        self.log.log("cost", f"Cost sources: {n} document(s) cited via "
                             f"{'Senso retrieval' if backend == 'senso' else 'bundled local copies' if backend == 'bundled' else 'Senso with bundled fallback'}.",
                     "ok" if backend == "senso" else "info", run_id, {"backend": backend})
        return out, backend

    def _ems_jpy(self, grams: int) -> int:
        table = self.params["emsZone4RatesJpy"]
        for limit, price in table:
            if grams <= limit:
                return price
        return table[-1][1]

    async def estimate(self, cand: Candidate, category: str, budget_usd: float | None, destination: str,
                       price_observed: bool = True, run_id: str | None = None) -> LandedCost:
        p = self.params
        cites, backend = await self._citations(run_id)
        rate, fx_source, fx_as_of = await self.fx.usd_per_jpy(run_id)
        lc = LandedCost(destination=destination, budget_usd=budget_usd, fx_rate_usd_per_jpy=rate, fx_source=fx_source,
                        fx_as_of=fx_as_of, citation_backend=backend)
        if cand.price_jpy is None:
            lc.warnings.append("The item price is unknown, so no landed-cost total can be estimated.")
            return lc
        if not _is_us_destination(destination):
            lc.warnings.append(f"Landed-cost rules are only implemented for U.S. destinations; no total is estimated "
                               f"for {destination}.")
            return lc

        def usd(jpy: float) -> float:
            return round(jpy * rate, 2)

        def ids(key: str) -> list[str]:
            return [c.id for c in cites.get(key, [])]

        item_usd = usd(cand.price_jpy)
        lines: list[CostLine] = [CostLine(
            key="item", label="Item price", amount_usd=item_usd, amount_jpy=cand.price_jpy,
            basis=("Price read from the store listing" if price_observed else
                   "Price stated in the X post; not confirmed on a store page"),
            confidence="observed" if price_observed else "assumption")]

        dom = p["assumedDomesticShippingJpy"]
        lines.append(CostLine(key="domestic_shipping", label="Shipping within Japan (to proxy warehouse)",
                              amount_usd=usd(dom), amount_jpy=dom,
                              basis="KAI assumption; the store's actual shipping fee was not read", confidence="assumption"))
        fee = p["assumedProxyServiceFeeJpy"]
        lines.append(CostLine(key="proxy_fee", label="Proxy / forwarding service fee", amount_usd=usd(fee), amount_jpy=fee,
                              basis="KAI assumption; fees differ by service and are not sourced", confidence="assumption"))

        grams = p["assumedPackedWeightGramsByCategory"].get(category, p["assumedPackedWeightGramsByCategory"]["default"])
        ems = self._ems_jpy(grams)
        lines.append(CostLine(key="intl_shipping", label="International shipping (Japan Post EMS to U.S.)",
                              amount_usd=usd(ems), amount_jpy=ems,
                              basis=f"Published EMS Zone 4 rate for the bracket covering an assumed packed weight of {grams} g",
                              confidence="sourced", citation_ids=ids("shipping")))

        duty_rate = p["dutyRateJapanOrigin"]
        duty = round(item_usd * duty_rate, 2)
        lines.append(CostLine(key="duty", label=f"U.S. import duty ({duty_rate:.1%} of item value)", amount_usd=duty,
                              basis="De minimis exemption suspended; Japan-origin goods capped at 12.5% total "
                                    "(MFN + Section 301) unless the product's own tariff rate is higher. "
                                    "KAI does not classify the product, so the cap is applied as the estimate.",
                              confidence="sourced", citation_ids=ids("duty")))
        clearance = round(p["clearanceFeeFixedUsd"] + p["clearanceFeeShareOfDuty"] * duty, 2)
        lines.append(CostLine(key="clearance", label="Duty prepayment / clearance fee", amount_usd=clearance,
                              basis=f"${p['clearanceFeeFixedUsd']:.2f} + {p['clearanceFeeShareOfDuty']:.0%} of duty, as published "
                                    "by one Japanese exporter for DDP postal shipments; other shippers may differ",
                              confidence="sourced", citation_ids=ids("clearance")))

        if "california" in destination.lower() or destination.strip().upper().startswith("CA"):
            tax_rate = self.settings.ca_use_tax_rate
            tax = round(item_usd * tax_rate, 2)
            lines.append(CostLine(key="use_tax", label=f"California use tax ({tax_rate:.2%}, self-reported)", amount_usd=tax,
                                  basis="Use tax applies to foreign purchases brought into California (cited). The rate is "
                                        "a configurable assumption (CA_USE_TAX_RATE); look up your local rate.",
                                  confidence="assumption", citation_ids=ids("use_tax")))
        else:
            lc.warnings.append(f"No sales/use tax estimate is implemented for {destination}; local taxes are excluded.")

        jpy_part = sum(l.amount_usd for l in lines if l.amount_jpy is not None)
        buffer = round(jpy_part * p["fxBufferShare"], 2)
        lines.append(CostLine(key="fx_buffer", label=f"Currency conversion buffer ({p['fxBufferShare']:.0%})", amount_usd=buffer,
                              basis="KAI assumption covering card / proxy exchange-rate markups", confidence="assumption"))

        lc.lines = lines
        lc.total_usd = round(sum(l.amount_usd for l in lines), 2)
        seen: dict[str, Citation] = {}
        for l in lines:
            for cid in l.citation_ids:
                for c in cites.get({"intl_shipping": "shipping"}.get(l.key, l.key), []):
                    if c.id == cid:
                        seen.setdefault(c.id, c)
        lc.citations = list(seen.values())
        if budget_usd is not None:
            lc.within_budget = "yes" if lc.total_usd <= budget_usd else "no"
        if fx_as_of is None:
            lc.warnings.append("Exchange rate is a configured fallback, not a live rate.")
        if not price_observed:
            lc.warnings.append("Item price comes from the post, not a store page.")
        if cand.ships_internationally == "no":
            lc.warnings.append("The store ships within Japan only, so a proxy or forwarding service is required.")
        lc.warnings.append("Estimate only: actual duty depends on tariff classification, and shipping, proxy and tax "
                           "amounts depend on the service and your address.")
        return lc
