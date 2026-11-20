"""Runtime switches. Offline by default: no paid or live network calls unless SHOPPER_LIVE=1."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
DATA_DIR = Path(os.environ.get("SHOPPER_DATA_DIR", ROOT / ".shopper-data"))


def data_dir() -> Path:
    return Path(os.environ.get("SHOPPER_DATA_DIR", str(DATA_DIR)))


def live() -> bool:
    return os.environ.get("SHOPPER_LIVE") == "1"
