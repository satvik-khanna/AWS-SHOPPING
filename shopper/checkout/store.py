"""Where checkout sessions and pending approval tokens live.

`claim(token_hash)` is the single-use primitive: it returns the session id at most once,
even with concurrent callers (lock in memory, atomic rename on disk).
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from .machine import Session

_HEX = re.compile(r"^[0-9a-f]{64}$")


class InMemorySessionStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._tokens: dict[str, str] = {}      # token hash -> session id

    def save(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.id] = session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def register_token(self, token_hash: str, session_id: str) -> None:
        with self._lock:
            self._tokens[token_hash] = session_id

    def peek(self, token_hash: str) -> str | None:
        """Session id for a pending token, without using it up."""
        return self._tokens.get(token_hash)

    def claim(self, token_hash: str) -> str | None:
        with self._lock:
            return self._tokens.pop(token_hash, None)

    def list(self) -> list[Session]:
        return sorted(self._sessions.values(), key=lambda s: s.created_at)


class FileSessionStore:
    """One JSON file per session, one file per pending token (named by its sha256).
    Lets `python3 -m shopper approve <token>` work in a later process."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, name: str) -> Path:
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, session: Session) -> None:
        path = self._dir("sessions") / f"{session.id}.json"
        tmp = path.with_suffix(f".tmp{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def get(self, session_id: str) -> Session | None:
        if not re.fullmatch(r"[0-9a-f]{1,64}", session_id or ""):
            return None
        path = self._dir("sessions") / f"{session_id}.json"
        if not path.exists():
            return None
        return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def register_token(self, token_hash: str, session_id: str) -> None:
        (self._dir("tokens") / token_hash).write_text(session_id, encoding="utf-8")

    def peek(self, token_hash: str) -> str | None:
        if not _HEX.match(token_hash or ""):
            return None
        try:
            return (self._dir("tokens") / token_hash).read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def claim(self, token_hash: str) -> str | None:
        if not _HEX.match(token_hash or ""):
            return None
        src = self._dir("tokens") / token_hash
        dst = self._dir("claimed") / token_hash
        try:
            os.rename(src, dst)          # atomic: only one caller can win
        except FileNotFoundError:
            return None
        return dst.read_text(encoding="utf-8").strip() or None

    def list(self) -> list[Session]:
        out = []
        for p in self._dir("sessions").glob("*.json"):
            try:
                out.append(Session.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (ValueError, TypeError):
                continue
        return sorted(out, key=lambda s: s.created_at)
