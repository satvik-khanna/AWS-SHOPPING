"""User memory. Base: local JSON file. TODO(memory pane): Cognee adapter behind SHOPPER_LIVE."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..config import DATA_DIR
from ..models import Listing, Preferences


class JsonMemory:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "memory.json"

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def recall(self, user_id: str) -> Preferences:
        row = self._load().get(user_id)
        return Preferences(**row) if row else Preferences(user_id=user_id)

    def _update(self, user_id: str, fn) -> None:
        data = self._load()
        prefs = Preferences(**data[user_id]) if user_id in data else Preferences(user_id=user_id)
        fn(prefs)
        data[user_id] = asdict(prefs)
        self._save(data)

    def remember_purchase(self, user_id: str, listing: Listing) -> None:
        self._update(user_id, lambda p: p.past_purchases.append(listing.id))

    def reject_seller(self, user_id: str, seller: str) -> None:
        self._update(user_id, lambda p: seller in p.rejected_sellers or p.rejected_sellers.append(seller))
