"""Truthful live check: reports what is configured and exercises only what can
actually run with the credentials present. Nothing here uses fixtures.

    uv run python scripts/live_check.py                       # status + FX + configured integrations
    uv run python scripts/live_check.py --listing <url>       # also import one real product page
    uv run python scripts/live_check.py --x-search keycaps    # one real X search (needs X_BEARER_TOKEN; costs money)

Uses a throwaway database so it never touches the app's data."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kai.app import build  # noqa: E402
from kai.config import Settings  # noqa: E402
from kai.xdiscovery.query_engine import build_queries  # noqa: E402


def line(name: str, state: str, detail: str = "") -> None:
    print(f"  {name:<28} {state:<22} {detail}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", help="a public product page URL to import")
    ap.add_argument("--x-search", metavar="CATEGORY", help="run one real X recent search for this category")
    args = ap.parse_args()

    settings = dataclasses.replace(Settings.from_env(), data_dir=Path(tempfile.mkdtemp(prefix="kai-live-")),
                                   allow_fixtures=False, x_max_queries_per_refresh=1)
    k = build(settings)

    print("Credentials")
    for label, present in [("X_BEARER_TOKEN", settings.x_bearer_token), ("BRAVE_SEARCH_API_KEY", settings.brave_api_key),
                           ("LLM key (OpenAI/Anthropic)", settings.openai_api_key or settings.anthropic_api_key),
                           ("SIE_API_KEY / SIE_BASE_URL", settings.sie_effective_base_url),
                           ("SENSO_API_KEY", settings.senso_api_key), ("LLM_API_KEY (Cognee)", settings.cognee_llm_api_key),
                           ("RAKUTEN_APP_ID + ACCESS_KEY", settings.rakuten_app_id and settings.rakuten_access_key)]:
        line(label, "set" if present else "not set")

    print("\nLive calls")
    rate, source, as_of = await k.costs.fx.usd_per_jpy()
    line("FX rate (Frankfurter)", "LIVE OK" if as_of else "FAILED -> fallback", f"1 JPY = {rate} USD, {source}, as of {as_of}"
         + (f" [{k.costs.fx.last_error}]" if k.costs.fx.last_error else ""))

    if settings.sie_effective_base_url:
        h = await k.sie.health()
        line("Superlinked SIE /v1/models", h["mode"].upper(), h.get("lastError") or f"{h.get('modelsListed')} model(s) listed")
        if h["mode"] == "live":
            _, backend = await k.sie.embed(["マット仕上げのアルチザンキーキャップ"])
            line("Superlinked SIE encode", "LIVE OK" if backend.startswith("sie:") else "FAILED", backend)
            scores, label = await k.sie.rerank("matte artisan keycap", [("a", "マット キーキャップ"), ("b", "カメラ レンズ")])
            line("Superlinked SIE score", "LIVE OK" if scores else "FAILED", f"{label} {scores or k.sie.state.last_error}")
    else:
        line("Superlinked SIE", "NOT ATTEMPTED", "no credentials")

    if settings.senso_api_key:
        w = await k.senso.whoami()
        line("Senso /org/me", w["mode"].upper(), w.get("lastError") or f"org: {w.get('org')}")
        cites, backend = await k.senso.cite("What import duty applies to Japan-origin goods entering the United States?",
                                            k.costs.params["dutySourceIds"])
        line("Senso search/context", "LIVE OK" if backend == "senso" else "NO MATCH -> bundled",
             f"{len(cites)} citation(s) via {backend} (run the seed action first if this is 'bundled')")
    else:
        line("Senso", "NOT ATTEMPTED", "no credentials")

    if k.cognee.state.configured:
        ok = await k.cognee.remember("KAI live check: the user prefers handmade items with a matte finish.", "livecheck")
        line("Cognee remember", "LIVE OK" if ok else "FAILED", k.cognee.state.last_error or "")
        if ok:
            got = await k.cognee.recall("What finish does the user prefer?", "livecheck")
            line("Cognee recall", "LIVE OK" if got else "FAILED/EMPTY", str(got)[:160])
    else:
        line("Cognee", "NOT ATTEMPTED", k.cognee.state.to_json()["detail"])

    if k.llm.available:
        out = await k.llm.complete_json("Reply with a JSON object.", 'Return {"ok": true}', max_tokens=20, purpose="live_check")
        line("LLM", "LIVE OK" if out else "FAILED", str(out or k.llm.last_error))
    else:
        line("LLM", "NOT ATTEMPTED", "no credentials")

    if args.x_search:
        if not settings.x_bearer_token:
            line("X recent search", "NOT ATTEMPTED", "X_BEARER_TOKEN is not set")
        else:
            posts, st = await k.official.search(build_queries(args.x_search))
            line("X recent search", st.state.upper(), f"{len(posts)} post(s), est. ${st.cost_usd_estimate:.3f}. {st.error or ''}")
            for p in posts[:5]:
                print(f"      {p.url}  {p.text[:70]!r}  links={[l.target for l in p.links]}")
    else:
        line("X recent search", "NOT REQUESTED" if settings.x_bearer_token else "NOT ATTEMPTED",
             "pass --x-search <category>" if settings.x_bearer_token else "X_BEARER_TOKEN is not set")

    if args.listing:
        cand, err = await k.marketplace.import_url(args.listing)
        if cand:
            line("Listing import", "LIVE OK", f"{cand.title!r} ¥{cand.price_jpy} {cand.availability} store={cand.store_name!r} "
                                              f"type={cand.store_type} intl={cand.ships_internationally} restrictions={cand.restrictions}")
        else:
            line("Listing import", "FAILED", err or "")

    await k.fetcher.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
