"""JSON-backed StoreRegistry for 1-URL creator/merchant storefronts."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from ..config import DATA_DIR
from .models import StoreProfile


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "store"


DEFAULT_STORE = StoreProfile(
    slug="tokyo-camera",
    name="Tokyo Camera Archive",
    source_url="https://linktr.ee/tokyo-camera-archive",
    bio="Curated Japanese cameras & niche collectibles with cited landed-cost quotes.",
    ship_from="JP",
    default_ship_to="US-CA",
    query_filter="Fujifilm X100V",
    tags=["camera", "japan", "vintage"],
)


class JsonStoreRegistry:
    """Stores creator/shop link registrations locally (offline by default)."""

    def __init__(self, path: Path | None = None, seed_default: bool = True):
        self.path = path or (DATA_DIR / "stores.json")
        if seed_default and not self._load():
            self.register(DEFAULT_STORE)

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _save(self, raw: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))

    def register(self, profile: StoreProfile) -> StoreProfile:
        slug = slugify(profile.slug or profile.name)
        saved = StoreProfile(
            slug=slug,
            name=profile.name,
            source_url=profile.source_url,
            bio=profile.bio,
            ship_from=profile.ship_from or "JP",
            default_ship_to=profile.default_ship_to or "US-CA",
            query_filter=profile.query_filter,
            tags=list(profile.tags),
        )
        raw = self._load()
        raw[slug] = asdict(saved)
        self._save(raw)
        return saved

    def get(self, slug: str) -> StoreProfile | None:
        d = self._load().get(slug)
        return StoreProfile(**d) if d else None

    def list_all(self) -> list[StoreProfile]:
        return [StoreProfile(**v) for v in self._load().values()]
