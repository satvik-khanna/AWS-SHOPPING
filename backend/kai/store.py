"""Small SQLite-backed document store, event log, usage ledger and TTL cache."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .models import new_id, now_iso


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS docs (
                    kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, id));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, ts TEXT, run_id TEXT,
                    stage TEXT, status TEXT, message TEXT, data TEXT);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id);
                CREATE TABLE IF NOT EXISTS usage (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, service TEXT, operation TEXT,
                    units REAL, unit_name TEXT, cost_usd REAL, cost_known INTEGER, meta TEXT);
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL, stored_at TEXT);
                """
            )
            self._db.commit()

    # ---- documents -------------------------------------------------------
    def hide_sample_documents(self) -> None:
        """Quarantine legacy demo records so old samples cannot re-enter live feeds.

        Preserve originals in a separate table, including mixed historical runs.
        Preferences, watched creators, and usage history are retained.
        """
        with self._lock:
            self._db.execute("CREATE TABLE IF NOT EXISTS sample_archive AS SELECT * FROM docs WHERE 0")
            rows = self._db.execute("SELECT kind,id,data FROM docs").fetchall()
            def sample(value: Any) -> bool:
                if isinstance(value, dict):
                    return bool(value.get("isFixture") or value.get("is_fixture")) or any(sample(v) for v in value.values())
                if isinstance(value, list):
                    return any(sample(v) for v in value)
                return isinstance(value, str) and "kai-fixture.invalid" in value
            for row in rows:
                if row["kind"] not in {"preference", "watched", "search_history"} and sample(json.loads(row["data"])):
                    self._db.execute("INSERT INTO sample_archive SELECT * FROM docs WHERE kind=? AND id=?",
                                     (row["kind"], row["id"]))
                    self._db.execute("DELETE FROM docs WHERE kind=? AND id=?", (row["kind"], row["id"]))
            # Old test page responses and generated FX values must not survive a mode switch.
            self._db.execute("DELETE FROM cache")
            self._db.commit()

    def put(self, kind: str, id: str, data: dict[str, Any]) -> None:
        ts = now_iso()
        with self._lock:
            self._db.execute(
                "INSERT INTO docs(kind,id,data,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (kind, id, json.dumps(data, ensure_ascii=False), ts, ts),
            )
            self._db.commit()

    def get(self, kind: str, id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM docs WHERE kind=? AND id=?", (kind, id)).fetchone()
        return json.loads(row["data"]) if row else None

    def list(self, kind: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT data FROM docs WHERE kind=? ORDER BY updated_at DESC, rowid DESC LIMIT ?", (kind, limit)
            ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def delete(self, kind: str, id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM docs WHERE kind=? AND id=?", (kind, id))
            self._db.commit()
        return cur.rowcount > 0

    # ---- events ----------------------------------------------------------
    def add_event(self, stage: str, message: str, status: str = "info", run_id: str | None = None,
                  data: dict[str, Any] | None = None) -> dict[str, Any]:
        ev = {"id": new_id("ev"), "ts": now_iso(), "runId": run_id, "stage": stage,
              "status": status, "message": message, "data": data}
        with self._lock:
            self._db.execute(
                "INSERT INTO events(id,ts,run_id,stage,status,message,data) VALUES(?,?,?,?,?,?,?)",
                (ev["id"], ev["ts"], run_id, stage, status, message,
                 json.dumps(data, ensure_ascii=False, default=str) if data is not None else None),
            )
            self._db.commit()
        return ev

    def events(self, run_id: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        q = "SELECT * FROM events"
        args: tuple = ()
        if run_id:
            q += " WHERE run_id=?"
            args = (run_id,)
        q += " ORDER BY seq DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(q, args + (limit,)).fetchall()
        out = [
            {"id": r["id"], "ts": r["ts"], "runId": r["run_id"], "stage": r["stage"], "status": r["status"],
             "message": r["message"], "data": json.loads(r["data"]) if r["data"] else None}
            for r in rows
        ]
        return out

    # ---- usage -----------------------------------------------------------
    def add_usage(self, service: str, operation: str, units: float, unit_name: str,
                  cost_usd: float | None, meta: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO usage(ts,service,operation,units,unit_name,cost_usd,cost_known,meta) VALUES(?,?,?,?,?,?,?,?)",
                (now_iso(), service, operation, units, unit_name, cost_usd or 0.0,
                 0 if cost_usd is None else 1, json.dumps(meta or {}, ensure_ascii=False, default=str)),
            )
            self._db.commit()

    def spend(self, service: str) -> float:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(cost_usd),0) AS s FROM usage WHERE service=?", (service,)).fetchone()
        return float(row["s"])

    def usage_summary(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT service, operation, unit_name, COUNT(*) AS calls, SUM(units) AS units, "
                "SUM(cost_usd) AS cost_usd, MIN(cost_known) AS cost_known, MAX(ts) AS last_ts "
                "FROM usage GROUP BY service, operation, unit_name ORDER BY service, operation"
            ).fetchall()
        return [
            {"service": r["service"], "operation": r["operation"], "unitName": r["unit_name"], "calls": r["calls"],
             "units": r["units"], "costUsd": round(r["cost_usd"] or 0.0, 6), "costKnown": bool(r["cost_known"]),
             "lastTs": r["last_ts"]}
            for r in rows
        ]

    # ---- cache -----------------------------------------------------------
    def cache_get(self, key: str) -> tuple[Any, str] | None:
        with self._lock:
            row = self._db.execute("SELECT value, expires_at, stored_at FROM cache WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            if row["expires_at"] < time.time():
                self._db.execute("DELETE FROM cache WHERE key=?", (key,))
                self._db.commit()
                return None
        return json.loads(row["value"]), row["stored_at"]

    def cache_set(self, key: str, value: Any, ttl_s: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO cache(key,value,expires_at,stored_at) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at, stored_at=excluded.stored_at",
                (key, json.dumps(value, ensure_ascii=False, default=str), time.time() + ttl_s, now_iso()),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
