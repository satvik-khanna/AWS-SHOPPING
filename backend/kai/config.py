"""Runtime settings. Everything is optional; missing credentials produce
explicit fallback states rather than errors."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent


def _s(env: Mapping[str, str], key: str) -> str | None:
    v = (env.get(key) or "").strip()
    return v or None


def _f(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(env[key]) if (env.get(key) or "").strip() else default
    except ValueError:
        return default


def _b(env: Mapping[str, str], key: str, default: bool) -> bool:
    v = (env.get(key) or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path = ROOT / "var"

    # X official API
    x_bearer_token: str | None = None
    x_api_base: str = "https://api.x.com"
    x_max_spend_usd: float = 1.00
    x_cost_per_post_usd: float = 0.005
    x_cost_per_user_usd: float = 0.010
    x_max_results_per_query: int = 10
    x_max_queries_per_refresh: int = 4
    x_cache_ttl_s: int = 900

    # Indexed web search (Brave)
    brave_api_key: str | None = None
    brave_api_base: str = "https://api.search.brave.com"

    # LLM
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str | None = None
    llm_model: str | None = None
    llm_input_usd_per_mtok: float | None = None
    llm_output_usd_per_mtok: float | None = None

    # Superlinked SIE
    sie_api_key: str | None = None
    sie_base_url: str | None = None
    sie_embed_model: str = "BAAI/bge-m3"
    sie_rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # Senso
    senso_api_key: str | None = None
    senso_base_url: str = "https://apiv2.senso.ai/api/v1"

    # Cognee
    cognee_enabled: str = "auto"  # auto | true | false
    cognee_llm_api_key: str | None = None

    # Rakuten
    rakuten_app_id: str | None = None
    rakuten_access_key: str | None = None
    rakuten_referer: str | None = None

    # Behaviour
    allow_fixtures: bool = True
    fetch_timeout_s: float = 8.0
    fetch_max_bytes: int = 1_500_000
    fetch_max_per_run: int = 12
    fetch_concurrency: int = 3
    fetch_min_interval_s: float = 0.7
    respect_robots: bool = True
    fx_fallback_usd_per_jpy: float = 0.0070
    fx_live: bool = True
    ca_use_tax_rate: float = 0.1025

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kai.sqlite3"

    @property
    def sie_effective_base_url(self) -> str | None:
        if self.sie_base_url:
            return self.sie_base_url.rstrip("/")
        if self.sie_api_key:
            return "https://api.superlinked.com"
        return None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        if env is None:
            try:
                from dotenv import load_dotenv

                load_dotenv(ROOT / ".env")
            except Exception:  # pragma: no cover - dotenv is a declared dependency
                pass
            env = os.environ
        d = cls()
        data_dir = Path(_s(env, "KAI_DATA_DIR") or "var")
        if not data_dir.is_absolute():
            data_dir = ROOT / data_dir
        return cls(
            data_dir=data_dir,
            x_bearer_token=_s(env, "X_BEARER_TOKEN"),
            x_max_spend_usd=_f(env, "X_API_MAX_SPEND_USD", d.x_max_spend_usd),
            x_cost_per_post_usd=_f(env, "X_COST_PER_POST_USD", d.x_cost_per_post_usd),
            x_cost_per_user_usd=_f(env, "X_COST_PER_USER_USD", d.x_cost_per_user_usd),
            x_max_results_per_query=int(_f(env, "X_MAX_RESULTS_PER_QUERY", d.x_max_results_per_query)),
            x_max_queries_per_refresh=int(_f(env, "X_MAX_QUERIES_PER_REFRESH", d.x_max_queries_per_refresh)),
            x_cache_ttl_s=int(_f(env, "X_CACHE_TTL_S", d.x_cache_ttl_s)),
            brave_api_key=_s(env, "BRAVE_SEARCH_API_KEY"),
            openai_api_key=_s(env, "OPENAI_API_KEY"),
            openai_base_url=(_s(env, "OPENAI_BASE_URL") or d.openai_base_url).rstrip("/"),
            anthropic_api_key=_s(env, "ANTHROPIC_API_KEY"),
            llm_model=_s(env, "LLM_MODEL"),
            llm_input_usd_per_mtok=_f(env, "LLM_INPUT_USD_PER_MTOK", -1) if _s(env, "LLM_INPUT_USD_PER_MTOK") else None,
            llm_output_usd_per_mtok=_f(env, "LLM_OUTPUT_USD_PER_MTOK", -1) if _s(env, "LLM_OUTPUT_USD_PER_MTOK") else None,
            sie_api_key=_s(env, "SIE_API_KEY"),
            sie_base_url=_s(env, "SIE_BASE_URL"),
            sie_embed_model=_s(env, "SIE_EMBED_MODEL") or d.sie_embed_model,
            sie_rerank_model=_s(env, "SIE_RERANK_MODEL") or d.sie_rerank_model,
            senso_api_key=_s(env, "SENSO_API_KEY"),
            senso_base_url=(_s(env, "SENSO_BASE_URL") or d.senso_base_url).rstrip("/"),
            cognee_enabled=(_s(env, "COGNEE_ENABLED") or "auto").lower(),
            cognee_llm_api_key=_s(env, "LLM_API_KEY"),
            rakuten_app_id=_s(env, "RAKUTEN_APP_ID"),
            rakuten_access_key=_s(env, "RAKUTEN_ACCESS_KEY"),
            rakuten_referer=_s(env, "RAKUTEN_REFERER"),
            allow_fixtures=_b(env, "KAI_ALLOW_FIXTURES", True),
            fetch_max_per_run=int(_f(env, "KAI_FETCH_MAX_PER_RUN", d.fetch_max_per_run)),
            respect_robots=_b(env, "KAI_RESPECT_ROBOTS", True),
            fx_fallback_usd_per_jpy=_f(env, "FX_FALLBACK_USD_PER_JPY", d.fx_fallback_usd_per_jpy),
            fx_live=_b(env, "KAI_FX_LIVE", True),
            ca_use_tax_rate=_f(env, "CA_USE_TAX_RATE", d.ca_use_tax_rate),
        )
