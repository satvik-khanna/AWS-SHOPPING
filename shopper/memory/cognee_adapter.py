"""Optional Cognee mirror. Only active when SHOPPER_LIVE=1, COGNEE_API_KEY (or LLM_API_KEY)
is set and the `cognee` package imports. The local JSON store stays the source of truth;
Cognee gets every write as a text memory so it can be searched semantically."""
from __future__ import annotations

import asyncio
import os

from ..config import live


class CogneeMirror:
    def __init__(self, base, cognee_mod):
        self.base = base
        self._c = cognee_mod

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _push(self, user_id: str, text: str) -> None:
        try:
            asyncio.run(self._c.add(text, dataset_name=f"shopper-{user_id}"))
        except Exception:
            pass   # mirror is best effort

    def recall(self, user_id):
        return self.base.recall(user_id)

    def remember_purchase(self, user_id, listing):
        self.base.remember_purchase(user_id, listing)
        self._push(user_id, f"User bought {listing.title_en} ({listing.id}) from seller {listing.seller}.")

    def reject_seller(self, user_id, seller):
        self.base.reject_seller(user_id, seller)
        self._push(user_id, f"User rejected seller {seller}.")


def maybe_wrap(base):
    if not (live() and (os.environ.get("COGNEE_API_KEY") or os.environ.get("LLM_API_KEY"))):
        return base
    try:
        import cognee  # type: ignore
    except Exception:
        return base
    return CogneeMirror(base, cognee)
