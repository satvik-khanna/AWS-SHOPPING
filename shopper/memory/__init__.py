"""User memory: past purchases, rejected sellers, condition standard, sizes, notes.
Base: local JSON file. Optional Cognee mirror behind SHOPPER_LIVE (see cognee_adapter.py)."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, fields
from pathlib import Path

from ..config import data_dir
from ..models import Listing, Preferences

CONDITIONS = ["junk", "fair", "good", "like_new", "new"]
_PREF_KEYS = {f.name for f in fields(Preferences)}
_lock = threading.Lock()


class JsonMemory:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else data_dir() / "memory.json"

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                return {}
        return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".tmp{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _prefs(user_id: str, row: dict | None) -> Preferences:
        row = {k: v for k, v in (row or {}).items() if k in _PREF_KEYS}
        row["user_id"] = user_id
        return Preferences(**row)

    def recall(self, user_id: str) -> Preferences:
        return self._prefs(user_id, self._load().get(user_id))

    def _update(self, user_id: str, fn) -> Preferences:
        with _lock:
            data = self._load()
            prefs = self._prefs(user_id, data.get(user_id))
            fn(prefs)
            data[user_id] = asdict(prefs)
            self._save(data)
            return prefs

    # MemoryStore protocol
    def remember_purchase(self, user_id: str, listing: Listing) -> None:
        def f(p):
            if listing.id not in p.past_purchases:
                p.past_purchases.append(listing.id)
                p.notes.append(f"bought {listing.id}: {listing.title_en} from {listing.seller}")
        self._update(user_id, f)

    def reject_seller(self, user_id: str, seller: str) -> None:
        self._update(user_id, lambda p: seller in p.rejected_sellers or p.rejected_sellers.append(seller))

    # extras
    def set_condition_min(self, user_id: str, condition: str | None) -> None:
        if condition is not None and condition not in CONDITIONS:
            raise ValueError(f"condition must be one of {CONDITIONS}")
        self._update(user_id, lambda p: setattr(p, "condition_min", condition))

    def set_size(self, user_id: str, kind: str, size: str) -> None:
        self._update(user_id, lambda p: p.sizes.__setitem__(kind, size))

    def add_note(self, user_id: str, note: str) -> None:
        self._update(user_id, lambda p: p.notes.append(note[:300]))

    def forget(self, user_id: str) -> None:
        with _lock:
            data = self._load()
            data.pop(user_id, None)
            self._save(data)


def build_memory():
    """JsonMemory, mirrored to Cognee when SHOPPER_LIVE=1 and COGNEE key present."""
    base = JsonMemory()
    from .cognee_adapter import maybe_wrap
    return maybe_wrap(base)


__all__ = ["JsonMemory", "CONDITIONS", "build_memory"]
