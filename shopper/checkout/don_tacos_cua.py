"""Computer Use driver backed by don-tacos's macOS Accessibility helper (`dt-ax`).

Stdlib only. Never steals focus, never moves the cursor, never types into password fields,
and never clicks final payment buttons without an explicit ApprovalGrant.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .drivers import BUYEE_CART, buyee_url
from .machine import ApprovalGrant

TIMEOUT = 20


def ax_path() -> str | None:
    """Resolve `dt-ax` binary via DT_AX env or standard don-tacos sibling workspace."""
    env = os.environ.get("DT_AX")
    if env:
        return env if os.path.isfile(env) and os.access(env, os.X_OK) else None
    candidates = [
        Path.home() / "orca" / "workspaces" / "gemini-hack" / "flatback" / "don-tacos" / "mac" / "build" / "dt-ax",
        Path.home() / "don-tacos" / "mac" / "build" / "dt-ax",
    ]
    for p in candidates:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _run(args: list[str]) -> dict[str, Any]:
    exe = ax_path()
    if not exe:
        raise RuntimeError("dt-ax helper not found (set DT_AX or build don-tacos/mac/build/dt-ax)")
    r = subprocess.run([exe, *args], capture_output=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    raw = r.stdout.decode("utf-8", "replace")
    data = json.loads(raw) if raw.strip() else {}
    if r.returncode != 0 or "error" in data:
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        raise RuntimeError(str(err.get("message") or raw or "dt-ax failed"))
    return data


def probe() -> dict[str, Any]:
    """Check whether don-tacos `dt-ax` is installed and Accessibility-trusted."""
    exe = ax_path()
    if not exe:
        return {"driver": "don-tacos:dt-ax", "installed": False, "trusted": False, "ok": False}
    try:
        trusted = bool(_run(["trusted"]).get("trusted"))
        return {"driver": "don-tacos:dt-ax", "installed": True, "trusted": trusted, "ok": trusted}
    except Exception as e:
        return {"driver": "don-tacos:dt-ax", "installed": True, "trusted": False, "ok": False, "error": str(e)}


class DonTacosChromeDriver:
    """Opens a new Chrome window via `dt-ax open-url`, inspects controls with `dt-ax tree`,
    clicks Add to Cart via `dt-ax press`, and pauses before payment for human Swipe-to-Pay."""
    name = "don-tacos-dt-ax-chrome"

    def __init__(self, app: str = "Google Chrome"):
        self.app = app
        self._sessions: dict[str, dict[str, Any]] = {}

    def open_listing(self, session_id: str, listing: dict) -> str:
        fields = listing.get("fields") if isinstance(listing.get("fields"), dict) else {}
        url = str(fields.get("buyee_url") or "") or buyee_url(listing)
        opened = _run(["open-url", self.app, url])
        self._sessions[session_id] = {
            "pid": int(opened["pid"]),
            "window": f"id:{opened['id']}" if opened.get("id") is not None else str(opened.get("window", 0)),
            "title": str(opened.get("title") or ""),
            "url": url,
        }
        return url

    def inspect_window(self, session_id: str) -> dict[str, Any]:
        sess = self._sessions.get(session_id)
        if not sess:
            return {}
        tree = _run(["tree", str(sess["pid"]), "--window", sess["window"]])
        controls = []
        for win in tree.get("windows") or []:
            for el in win.get("elements") or []:
                label = str(el.get("label") or "").strip()
                if label and el.get("role") in ("AXButton", "AXLink", "AXTextField"):
                    controls.append({"role": el.get("role"), "label": label[:80], "ref": el.get("ref")})
        return {
            "pid": sess["pid"],
            "window": sess["window"],
            "title": sess.get("title") or "",
            "url": sess.get("url") or "",
            "controls": controls[:12],
            "controls_count": len(controls),
        }

    def add_to_cart(self, session_id: str, listing: dict) -> None:
        sess = self._sessions.get(session_id)
        if not sess:
            raise RuntimeError("no Chrome window opened for session")
        tree = _run(["tree", str(sess["pid"]), "--window", sess["window"]])
        pattern = re.compile(r"add to cart|カートに入れる|buy now|購入手続き", re.I)
        for win in tree.get("windows") or []:
            for el in win.get("elements") or []:
                role = el.get("role", "")
                label = el.get("label", "")
                if role in ("AXButton", "AXLink") and pattern.search(label):
                    _run(["press", str(sess["pid"]), el["ref"], "--window", sess["window"]])
                    return
        raise RuntimeError("Add to Cart button not found in Chrome accessibility tree")

    def read_cart(self, session_id: str) -> tuple[list[dict], str]:
        sess = self._sessions.get(session_id) or {}
        return [{"driver": self.name, "window": sess.get("window")}], BUYEE_CART

    def place(self, session_id: str, grant: ApprovalGrant) -> str:
        if not isinstance(grant, ApprovalGrant) or grant.session_id != session_id or grant.used:
            raise PermissionError("place() needs an unused approval grant for this session")
        grant.used = True
        return "HANDOFF:" + BUYEE_CART
