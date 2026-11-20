"""Cognee adapter: graph memory for user preferences and search history.

Uses the Cognee v1 Python API:
    await cognee.remember(text, dataset_name=...)
    await cognee.recall(query_text, datasets=[...], top_k=...)

Cognee is an optional dependency (`uv sync --extra cognee`) and needs an LLM
key (LLM_API_KEY) to build its graph. When either is missing, or a call fails
or times out, KAI uses its local preference store and says so."""

from __future__ import annotations

import asyncio
import importlib.util
import os
from typing import Any

from ..config import Settings
from ..observability import ActivityLog
from . import IntegrationState

REMEMBER_TIMEOUT_S = 90.0
RECALL_TIMEOUT_S = 30.0


class CogneeAdapter:
    def __init__(self, settings: Settings, log: ActivityLog, module: Any | None = None):
        self.settings = settings
        self.log = log
        self._cognee = module  # injectable for tests
        self._populated = False
        installed = module is not None or importlib.util.find_spec("cognee") is not None
        has_key = bool(settings.cognee_llm_api_key)
        enabled = settings.cognee_enabled
        if enabled == "false":
            configured, why = False, "COGNEE_ENABLED=false; using the local preference store."
        elif not installed:
            configured, why = False, ("The cognee package is not installed (run `uv sync --extra cognee`); "
                                      "using the local preference store.")
        elif not has_key and module is None:
            configured, why = False, ("cognee is installed but LLM_API_KEY is not set, which Cognee needs to "
                                      "build its knowledge graph; using the local preference store.")
        else:
            configured, why = True, ""
        self.state = IntegrationState(
            name="cognee", label="Cognee (memory)", configured=configured,
            fallback_label="local preference store (SQLite, keyword recall)", unconfigured_detail=why,
            extra={"packageInstalled": installed},
        )

    def _mod(self) -> Any:
        if self._cognee is None:
            if self.settings.cognee_llm_api_key:
                os.environ.setdefault("LLM_API_KEY", self.settings.cognee_llm_api_key)
            import cognee  # type: ignore[import-not-found]

            self._cognee = cognee
        return self._cognee

    @staticmethod
    def dataset(user_id: str) -> str:
        return f"kai_user_{user_id}"

    async def remember(self, text: str, user_id: str = "default", run_id: str | None = None) -> bool:
        if not self.state.configured:
            self.state.used_fallback()
            return False
        try:
            await asyncio.wait_for(self._mod().remember(text, dataset_name=self.dataset(user_id)), REMEMBER_TIMEOUT_S)
            self._populated = True
            self.state.ok()
            self.log.usage("cognee", "remember", 1, "items", None, {"dataset": self.dataset(user_id)})
            return True
        except Exception as e:  # noqa: BLE001
            self.state.fail(f"{type(e).__name__}: {e}")
            self.log.log("memory", f"Cognee remember failed; kept in local store only. {self.state.last_error}", "warn", run_id)
            return False

    async def recall(self, query: str, user_id: str = "default", top_k: int = 5, run_id: str | None = None) -> list[str] | None:
        """Return recalled text snippets, or None when Cognee was not used."""
        if not self.state.configured:
            self.state.used_fallback()
            return None
        try:
            results = await asyncio.wait_for(
                self._mod().recall(query, datasets=[self.dataset(user_id)], top_k=top_k), RECALL_TIMEOUT_S)
            texts: list[str] = []
            for r in results or []:
                # Cognee 1.x returns typed entries: graph entries carry `text`, QA entries
                # `answer`, session-context entries `content`.
                t = None
                for field in ("text", "answer", "content"):
                    t = r.get(field) if isinstance(r, dict) else getattr(r, field, None)
                    if t:
                        break
                if t:
                    texts.append(str(t)[:800])
            self.state.ok()
            self.log.usage("cognee", "recall", 1, "queries", None, {"dataset": self.dataset(user_id)})
            return texts
        except Exception as e:  # noqa: BLE001
            # Recalling before anything was remembered raises a precondition error; that is
            # an empty memory, not an outage.
            name = type(e).__name__
            if not self._populated and name in {"RecallPreconditionError", "DatasetNotFoundError"}:
                self.state.extra["note"] = "Cognee memory is empty for this user."
                return []
            self.state.fail(f"{name}: {e}")
            self.log.log("memory", f"Cognee recall failed; using local preference store. {self.state.last_error}", "warn", run_id)
            return None
