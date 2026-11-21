"""Computer Use driver backed by don-tacos's macOS Accessibility helper (`dt-ax`). Live only.

Stdlib only. Never steals focus, never moves the cursor, never types into password fields
(dt-ax itself refuses secure fields too), never types a card number or CVV, and never presses
anything that moves toward ordering or paying before an ApprovalGrant exists:
  - before the grant: only add-to-cart buttons, the cart / checkout pages, shipping text fields
    and choosing an already-saved card by its last 4 digits;
  - after the grant (`place`): the final order button, and only with SHOPPER_ALLOW_LIVE_ORDER=1;
    otherwise the filled payment page is handed to the user.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .drivers import BUYEE_CART, buyee_url
from .guards import check_open_url
from .machine import ApprovalGrant

TIMEOUT = 20

# Pre-grant: add to cart only. Anything that moves toward ordering / paying is post-grant.
ADD_TO_CART = re.compile(r"add to cart|カートに入れる", re.I)
CHECKOUT_LINK = re.compile(r"proceed to checkout|go to checkout|^checkout$|レジに進む|手続きへ進む", re.I)
SIGN_IN = re.compile(r"sign ?in|log ?in|ログイン|パスワード|password", re.I)
PLACE_ORDER = re.compile(r"place (your )?order|confirm (order|purchase)|注文を確定|購入を確定|支払いを確定", re.I)
FIELD_LABELS = {
    "full_name": re.compile(r"full name|^name$|氏名|お名前", re.I),
    "address_line1": re.compile(r"address( line)? ?1|street|住所1|番地", re.I),
    "address_line2": re.compile(r"address( line)? ?2|apt|suite|建物", re.I),
    "city": re.compile(r"city|市区町村", re.I),
    "region": re.compile(r"state|province|region|都道府県", re.I),
    "postal_code": re.compile(r"zip|postal|郵便番号", re.I),
    "country": re.compile(r"country|国", re.I),
    "phone": re.compile(r"phone|電話", re.I),
}
_NEVER_TYPE = re.compile(r"pass|card|cvv|cvc|security|有効期限|カード番号", re.I)


def ax_path() -> str | None:
    """Resolve `dt-ax`: $DT_AX, then PATH, then don-tacos's install dir, then ~/don-tacos/mac/build/dt-ax."""
    env = os.environ.get("DT_AX")
    if env:
        return env if os.path.isfile(env) and os.access(env, os.X_OK) else None
    on_path = shutil.which("dt-ax")
    if on_path:
        return on_path
    installed = Path.home() / "Library" / "Application Support" / "don-tacos" / "dt-ax"  # don-tacos `mac/build.sh install`
    if installed.is_file() and os.access(installed, os.X_OK):
        return str(installed)
    p = Path.home() / "don-tacos" / "mac" / "build" / "dt-ax"
    return str(p) if p.is_file() and os.access(p, os.X_OK) else None


def _run(args: list[str]) -> dict[str, Any]:
    exe = ax_path()
    if not exe:
        raise RuntimeError("dt-ax helper not found (set DT_AX, put dt-ax on PATH, or build ~/don-tacos/mac/build/dt-ax)")
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
    """Opens a new Chrome window via `dt-ax open-url`, reads controls with `dt-ax tree`,
    presses with `dt-ax press`, types with `dt-ax set`, and stops at the payment step."""
    name = "don-tacos-dt-ax-chrome"
    has_checkout = True

    def __init__(self, app: str = "Google Chrome", run=None):
        self.app = app
        self._run = run or _run            # injectable for tests (no real Chrome in tests)
        self._sessions: dict[str, dict[str, Any]] = {}

    # ---- helpers -------------------------------------------------------------------
    def _sess(self, sid: str) -> dict[str, Any]:
        s = self._sessions.get(sid)
        if not s:
            raise RuntimeError("no Chrome window opened for session")
        return s

    def _elements(self, sid: str) -> list[dict]:
        s = self._sess(sid)
        tree = self._run(["tree", str(s["pid"]), "--window", s["window"]])
        return [el for win in tree.get("windows") or [] for el in win.get("elements") or []]

    def _press(self, sid: str, ref: str) -> None:
        s = self._sess(sid)
        self._run(["press", str(s["pid"]), ref, "--window", s["window"]])

    def _open(self, sid: str, url: str) -> dict:
        url = check_open_url(url)
        opened = self._run(["open-url", self.app, url])
        self._sessions[sid] = {
            "pid": int(opened["pid"]),
            "window": f"id:{opened['id']}" if opened.get("id") is not None else str(opened.get("window", 0)),
            "title": str(opened.get("title") or ""),
            "url": url,
        }
        return self._sessions[sid]

    # ---- pages (pre-grant) ------------------------------------------------------------
    def open_listing(self, session_id: str, listing: dict) -> str:
        fields = listing.get("fields") if isinstance(listing.get("fields"), dict) else {}
        url = str(fields.get("buyee_url") or "") or buyee_url(listing)
        self._open(session_id, url)      # raises UnsafeUrl before dt-ax is called
        return url

    def inspect_window(self, session_id: str) -> dict[str, Any]:
        sess = self._sessions.get(session_id)
        if not sess:
            return {}
        controls = []
        for el in self._elements(session_id):
            label = str(el.get("label") or "").strip()
            if label and el.get("role") in ("AXButton", "AXLink", "AXTextField"):
                controls.append({"role": el.get("role"), "label": label[:80], "ref": el.get("ref")})
        return {"pid": sess["pid"], "window": sess["window"], "title": sess.get("title") or "",
                "url": sess.get("url") or "", "controls": controls[:12], "controls_count": len(controls)}

    def add_to_cart(self, session_id: str, listing: dict) -> None:
        for el in self._elements(session_id):
            if el.get("role") in ("AXButton", "AXLink") and ADD_TO_CART.search(str(el.get("label") or "")):
                self._press(session_id, el["ref"])
                return
        raise RuntimeError("Add to Cart button not found in Chrome accessibility tree")

    def read_cart(self, session_id: str) -> tuple[list[dict], str]:
        sess = self._sess(session_id)
        return [{"driver": self.name, "window": sess.get("window")}], BUYEE_CART

    def open_checkout(self, session_id: str, listing: dict) -> dict:
        from .chrome import site_of
        s = self._open(session_id, BUYEE_CART)
        site = site_of(s["url"])
        els = self._elements(session_id)
        if any(el.get("secure") or SIGN_IN.search(str(el.get("label") or "")) for el in els
               if el.get("role") in ("AXTextField", "AXSecureTextField", "AXButton")):
            return {"page": "sign_in", "site": site, "url": s["url"]}
        for el in els:
            if el.get("role") in ("AXButton", "AXLink") and CHECKOUT_LINK.search(str(el.get("label") or "")):
                self._press(session_id, el["ref"])
                return {"page": "checkout", "site": site, "url": s["url"]}
        raise RuntimeError("checkout button not found on the cart page")

    def fill_shipping(self, session_id: str, fields: dict) -> list[str]:
        from ..vault import check_secret_free
        check_secret_free(fields)
        s = self._sess(session_id)
        filled = []
        for el in self._elements(session_id):
            label = str(el.get("label") or "")
            if el.get("role") != "AXTextField" or el.get("secure") or _NEVER_TYPE.search(label):
                continue
            for key, rx in FIELD_LABELS.items():
                if key in fields and key not in filled and rx.search(label):
                    self._run(["set", str(s["pid"]), el["ref"], str(fields[key]), "--window", s["window"]])
                    filled.append(key)
                    break
        return filled      # Buyee usually uses the address saved in the account: an empty list is fine

    def select_payment(self, session_id: str, pm: dict | None) -> str:
        if not pm:
            return "site default payment method"
        for el in self._elements(session_id):
            label = str(el.get("label") or "")
            if el.get("role") in ("AXRadioButton", "AXButton", "AXCell") and re.search(rf"\b{re.escape(pm['last4'])}\b", label) \
                    and not PLACE_ORDER.search(label):
                self._press(session_id, el["ref"])
                return f"{pm.get('brand')} ending {pm['last4']}"
        return f"{pm.get('brand')} ending {pm['last4']} not shown; site default kept"

    def read_item_price_jpy(self, session_id: str) -> int | None:
        return None        # not reliably readable from the accessibility tree; the bound amount is used

    # ---- post-grant ------------------------------------------------------------------
    def place(self, session_id: str, grant: ApprovalGrant) -> str:
        if not isinstance(grant, ApprovalGrant) or grant.session_id != session_id or grant.used:
            raise PermissionError("place() needs an unused approval grant for this session")
        grant.used = True
        if os.environ.get("SHOPPER_ALLOW_LIVE_ORDER") != "1":
            return "HANDOFF:" + BUYEE_CART
        for el in self._elements(session_id):
            if el.get("role") == "AXButton" and PLACE_ORDER.search(str(el.get("label") or "")):
                self._press(session_id, el["ref"])
                return f"LIVE:{session_id[:8]}"
        return "HANDOFF:" + BUYEE_CART
