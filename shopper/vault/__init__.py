"""Per-user vault: shipping address, sites the user's Chrome profile is signed into, and
payment method references. File: <data_dir>/vault/vault.json, dir 0700, file 0600 (gitignored).

Refuses anything that looks like a password, a card number (13-19 digits passing Luhn) or a CVV.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, fields
from pathlib import Path

from ..models import PaymentMethodRef, ShippingAddress, VaultRecord

BRANDS = {"visa", "mastercard", "amex", "jcb", "discover", "diners", "unionpay", "paypal", "other"}
_FORBIDDEN_KEYS = re.compile(r"pass(word|wd|code)?|pwd|cvv|cvc|csc|security.?code|card.?number|pan\b|ccnum|pin\b", re.I)
_DIGITS = re.compile(r"(?:\d[ -]?){13,19}")
_lock = threading.Lock()


class VaultError(ValueError):
    pass


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def looks_like_card_number(text: str) -> bool:
    for m in _DIGITS.finditer(text or ""):
        d = re.sub(r"\D", "", m.group(0))
        if 13 <= len(d) <= 19 and _luhn(d):
            return True
    return False


def check_secret_free(obj, where: str = "") -> None:
    """Raise if a dict/str carries a password / card number / CVV."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _FORBIDDEN_KEYS.search(str(k)):
                raise VaultError(f"refusing to store or type field {where}{k!r}: passwords, card numbers and CVV are never kept")
            check_secret_free(v, f"{where}{k}.")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            check_secret_free(v, f"{where}{i}.")
    elif isinstance(obj, str) and looks_like_card_number(obj):
        raise VaultError(f"refusing {where.rstrip('.') or 'value'}: looks like a card number")


def normalize_site(site: str) -> str:
    s = (site or "").strip().lower()
    s = re.sub(r"^https?://", "", s).split("/", 1)[0]
    s = s[4:] if s.startswith("www.") else s
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", s):
        raise VaultError(f"bad site {site!r}")
    return s


def validate_address(a: ShippingAddress) -> ShippingAddress:
    d = asdict(a)
    for k, v in d.items():
        if not isinstance(v, str) or len(v) > 200:
            raise VaultError(f"address.{k} must be a string of at most 200 chars")
    for k in ("name", "line1", "city", "postal_code", "country"):
        if not d[k].strip():
            raise VaultError(f"address.{k} is required")
    if not re.fullmatch(r"[A-Za-z]{2}", d["country"]):
        raise VaultError("address.country must be a 2-letter code")
    check_secret_free(d, "address.")
    return ShippingAddress(**{**d, "country": d["country"].upper()})


def validate_payment(pm: PaymentMethodRef) -> PaymentMethodRef:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{6,128}", pm.token or "") or re.fullmatch(r"[\d\- ]+", pm.token):
        raise VaultError("payment token must be a provider reference like pm_xxx / tok_xxx, not a card number")
    if looks_like_card_number(pm.token) or looks_like_card_number(pm.label or ""):
        raise VaultError("payment method looks like a card number; store only token + brand + last4 + exp")
    brand = (pm.brand or "").lower()
    if brand not in BRANDS:
        raise VaultError(f"brand must be one of {sorted(BRANDS)}")
    if not re.fullmatch(r"\d{4}", pm.last4 or ""):
        raise VaultError("last4 must be exactly 4 digits")
    m = re.fullmatch(r"(0[1-9]|1[0-2])/(\d{2})", pm.exp or "")
    if not m:
        raise VaultError("exp must be MM/YY")
    if len(pm.label or "") > 60:
        raise VaultError("label too long")
    return PaymentMethodRef(token=pm.token, brand=brand, last4=pm.last4, exp=pm.exp, label=pm.label or "")


class JsonVault:
    def __init__(self, path: Path | None = None):
        if path is None:
            from ..config import data_dir
            path = data_dir() / "vault" / "vault.json"
        self.path = Path(path)

    # ---- file ------------------------------------------------------------------
    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    @staticmethod
    def _record(user_id: str, row: dict | None) -> VaultRecord:
        row = row or {}
        addr = row.get("address")
        return VaultRecord(
            user_id=user_id,
            address=ShippingAddress(**{k: v for k, v in addr.items() if k in {f.name for f in fields(ShippingAddress)}}) if addr else None,
            signed_in_sites=list(row.get("signed_in_sites") or []),
            payment_methods=[PaymentMethodRef(**p) for p in row.get("payment_methods") or []],
            default_payment=row.get("default_payment"),
        )

    def _update(self, user_id: str, fn) -> VaultRecord:
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", user_id or ""):
            raise VaultError("bad user id")
        with _lock:
            data = self._load()
            rec = self._record(user_id, data.get(user_id))
            fn(rec)
            row = asdict(rec)
            row.pop("user_id", None)
            check_secret_free(row)
            data[user_id] = row
            self._save(data)
            return rec

    # ---- Vault protocol ----------------------------------------------------------
    def get(self, user_id: str) -> VaultRecord:
        return self._record(user_id, self._load().get(user_id))

    def set_address(self, user_id: str, address: ShippingAddress) -> None:
        a = validate_address(address)
        self._update(user_id, lambda r: setattr(r, "address", a))

    def set_signed_in(self, user_id: str, site: str, signed_in: bool) -> None:
        s = normalize_site(site)

        def f(r):
            if signed_in and s not in r.signed_in_sites:
                r.signed_in_sites.append(s)
            if not signed_in and s in r.signed_in_sites:
                r.signed_in_sites.remove(s)
        self._update(user_id, f)

    def add_payment_method(self, user_id: str, pm: PaymentMethodRef, default: bool = True) -> None:
        p = validate_payment(pm)

        def f(r):
            r.payment_methods = [x for x in r.payment_methods if x.token != p.token] + [p]
            if default or not r.default_payment:
                r.default_payment = p.token
        self._update(user_id, f)

    def remove_payment_method(self, user_id: str, token: str) -> None:
        def f(r):
            r.payment_methods = [x for x in r.payment_methods if x.token != token]
            if r.default_payment == token:
                r.default_payment = r.payment_methods[-1].token if r.payment_methods else None
        self._update(user_id, f)

    def forget(self, user_id: str) -> None:
        with _lock:
            data = self._load()
            if data.pop(user_id, None) is not None:
                self._save(data)


class MemoryVault(JsonVault):
    """Same rules, kept in process memory (tests, firedrill)."""

    def __init__(self):
        self._data: dict = {}
        self.path = Path("<memory>")

    def _load(self) -> dict:
        return json.loads(json.dumps(self._data))

    def _save(self, data: dict) -> None:
        self._data = json.loads(json.dumps(data))


def default_payment(rec: VaultRecord) -> PaymentMethodRef | None:
    for p in rec.payment_methods:
        if p.token == rec.default_payment:
            return p
    return rec.payment_methods[-1] if rec.payment_methods else None


def public_view(rec: VaultRecord) -> dict:
    """Safe to show / log: no tokens."""
    d = asdict(rec)
    d["payment_methods"] = [{"brand": p.brand, "last4": p.last4, "exp": p.exp, "label": p.label,
                             "default": p.token == rec.default_payment} for p in rec.payment_methods]
    d.pop("default_payment", None)
    return d


__all__ = ["JsonVault", "MemoryVault", "VaultError", "check_secret_free", "looks_like_card_number", "normalize_site",
           "default_payment", "public_view"]
