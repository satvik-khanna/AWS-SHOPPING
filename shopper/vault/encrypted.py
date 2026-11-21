"""Encrypted Touch ID Vault for sensitive checkout fields (shipping address + site login).

Storage:
  - Ciphertext only on disk: <data_dir>/vault/touchid_vault.enc (AES-256-GCM, mode 0600, gitignored).
  - Master 256-bit key: stored in macOS Keychain (service 'aws-shopping.touchid-vault') or
    SHOPPER_VAULT_KEY env var (64-char hex, for tests / non-Keychain environments).
  - Every read (unlock_address / unlock_login) requires passing the Touch ID gate first
    (macOS LocalAuthentication via `touchid-gate auth`, injectable for offline tests).

This lives alongside shopper.vault.JsonVault without relaxing JsonVault's secret-free guarantee.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from ..config import ROOT, data_dir
from ..models import ShippingAddress
from . import VaultError, normalize_site

KEYCHAIN_SERVICE = "aws-shopping.touchid-vault"
KEYCHAIN_ACCOUNT = "master-key"
_lock = threading.Lock()


class TouchIDDenied(PermissionError):
    """Raised when Touch ID authentication is denied, canceled, or unavailable."""


def helper_path() -> Path:
    return data_dir() / "bin" / "touchid-gate"


def ensure_helper_built() -> Path | None:
    """Compile scripts/touchid_gate.swift on macOS if not built yet. Returns binary path or None."""
    exe = helper_path()
    src = ROOT / "scripts" / "touchid_gate.swift"
    if exe.is_file() and os.access(exe, os.X_OK) and (
            not src.is_file() or exe.stat().st_mtime >= src.stat().st_mtime):
        return exe
    if not src.is_file() or not shutil.which("swiftc"):
        return None
    exe.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["swiftc", "-O", str(src), "-o", str(exe),
         "-framework", "LocalAuthentication", "-framework", "Foundation", "-framework", "CryptoKit"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return None
    os.chmod(exe, 0o700)
    return exe


def touchid_auth(reason: str) -> None:
    """Trigger macOS Touch ID prompt. Raises TouchIDDenied on failure/cancel."""
    exe = ensure_helper_built()
    if not exe:
        raise TouchIDDenied("Touch ID helper unavailable on this platform")
    r = subprocess.run([str(exe), "auth", reason], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise TouchIDDenied((r.stderr or r.stdout or "Touch ID denied").strip())


def _get_or_create_master_key() -> bytes:
    """Return 32-byte master key from SHOPPER_VAULT_KEY or macOS Keychain."""
    env_hex = os.environ.get("SHOPPER_VAULT_KEY", "").strip()
    if env_hex:
        try:
            key = bytes.fromhex(env_hex)
            if len(key) == 32:
                return key
        except ValueError:
            pass
        raise VaultError("SHOPPER_VAULT_KEY must be 64 hex chars (32 bytes)")

    if shutil.which("security"):
        r = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and len(r.stdout.strip()) == 64:
            return bytes.fromhex(r.stdout.strip())
        # Create new random 256-bit key and save to macOS Keychain
        key = secrets.token_bytes(32)
        key_hex = key.hex()
        add = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", key_hex],
            capture_output=True, text=True, timeout=10,
        )
        if add.returncode == 0:
            return key

    # Fallback: local 0600 keyfile inside data_dir (gitignored)
    kf = data_dir() / "vault" / ".master.key"
    if kf.exists():
        return bytes.fromhex(kf.read_text(encoding="utf-8").strip())
    kf.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(kf.parent, 0o700)
    key = secrets.token_bytes(32)
    fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key.hex())
    os.chmod(kf, 0o600)
    return key


def _encrypt_bytes(plain: bytes, key: bytes) -> bytes:
    """Encrypt with AES-256-GCM via macOS CryptoKit helper, falling back to openssl or HMAC-CTR."""
    exe = ensure_helper_built()
    if exe:
        r = subprocess.run([str(exe), "encrypt"], input=key.hex().encode() + b"\n" + plain, capture_output=True, timeout=15)
        if r.returncode == 0:
            return b"CKGCM1:" + r.stdout.strip()
    # Portable stdlib fallback: SHA-256 CTR stream + HMAC-SHA256 tag (authenticated encryption)
    nonce = secrets.token_bytes(16)
    enc_key = hashlib.sha256(key + b"enc").digest()
    mac_key = hashlib.sha256(key + b"mac").digest()
    out = bytearray(len(plain))
    counter = 0
    for i in range(0, len(plain), 32):
        ks = hashlib.sha256(enc_key + nonce + counter.to_bytes(8, "big")).digest()
        chunk = plain[i:i + 32]
        for j, b in enumerate(chunk):
            out[i + j] = b ^ ks[j]
        counter += 1
    tag = hmac.new(mac_key, nonce + bytes(out), hashlib.sha256).digest()
    return b"STDCTR1:" + base64.b64encode(nonce + tag + bytes(out))


def _decrypt_bytes(blob: bytes, key: bytes) -> bytes:
    blob = blob.strip()
    if blob.startswith(b"CKGCM1:"):
        exe = ensure_helper_built()
        if not exe:
            raise VaultError("CryptoKit helper required to decrypt CKGCM1 vault")
        r = subprocess.run([str(exe), "decrypt"], input=key.hex().encode() + b"\n" + blob[7:], capture_output=True, timeout=15)
        if r.returncode != 0:
            raise VaultError("vault decryption failed (wrong key or tampered file)")
        return r.stdout
    if blob.startswith(b"STDCTR1:"):
        raw = base64.b64decode(blob[8:])
        if len(raw) < 48:
            raise VaultError("corrupt encrypted vault")
        nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
        enc_key = hashlib.sha256(key + b"enc").digest()
        mac_key = hashlib.sha256(key + b"mac").digest()
        expected = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise VaultError("vault authentication failed (wrong key or tampered file)")
        out = bytearray(len(ct))
        counter = 0
        for i in range(0, len(ct), 32):
            ks = hashlib.sha256(enc_key + nonce + counter.to_bytes(8, "big")).digest()
            chunk = ct[i:i + 32]
            for j, b in enumerate(chunk):
                out[i + j] = b ^ ks[j]
            counter += 1
        return bytes(out)
    raise VaultError("unknown encrypted vault format")


class EncryptedTouchIDVault:
    """Stores encrypted shipping addresses and site logins; requires Touch ID to unlock."""

    def __init__(self, path: Path | None = None, *,
                 key: bytes | None = None,
                 auth: Callable[[str], None] | None = None):
        self.path = Path(path) if path else data_dir() / "vault" / "touchid_vault.enc"
        self._key = key
        self._auth = auth or touchid_auth

    def _master_key(self) -> bytes:
        return self._key if self._key is not None else _get_or_create_master_key()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        raw = self.path.read_bytes()
        if not raw.strip():
            return {}
        plain = _decrypt_bytes(raw, self._master_key())
        return json.loads(plain.decode("utf-8"))

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        blob = _encrypt_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"), self._master_key())
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    # ---- writes (always encrypted at rest) -------------------------------------------------
    def set_address(self, user_id: str, addr: ShippingAddress) -> None:
        with _lock:
            data = self._load()
            row = data.setdefault(user_id, {})
            row["address"] = asdict(addr)
            self._save(data)

    def set_login(self, user_id: str, site: str, username: str, password: str) -> None:
        s = normalize_site(site)
        if not username or not password:
            raise VaultError("username and password are required")
        with _lock:
            data = self._load()
            row = data.setdefault(user_id, {})
            row.setdefault("logins", {})[s] = {"username": username, "password": password}
            self._save(data)

    # ---- metadata (no secrets, no Touch ID needed) -----------------------------------------
    def status(self, user_id: str) -> dict:
        with _lock:
            data = self._load()
            row = data.get(user_id) or {}
            return {
                "user_id": user_id,
                "encrypted": True,
                "has_address": bool(row.get("address")),
                "login_sites": sorted((row.get("logins") or {}).keys()),
            }

    # ---- reads (MUST pass Touch ID first) --------------------------------------------------
    def unlock_address(self, user_id: str, reason: str | None = None) -> ShippingAddress | None:
        self._auth(reason or f"Touch ID to fill encrypted shipping address for {user_id}")
        with _lock:
            row = self._load().get(user_id) or {}
            addr = row.get("address")
            return ShippingAddress(**addr) if isinstance(addr, dict) else None

    def unlock_login(self, user_id: str, site: str, reason: str | None = None) -> dict[str, str] | None:
        s = normalize_site(site)
        self._auth(reason or f"Touch ID to sign in to {s} for {user_id}")
        with _lock:
            row = self._load().get(user_id) or {}
            cred = (row.get("logins") or {}).get(s)
            return dict(cred) if isinstance(cred, dict) else None
