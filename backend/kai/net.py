"""Bounded, polite HTTP fetching for public storefront pages.

Guarantees:
- http/https only, public addresses only (no loopback/private/link-local)
- robots.txt respected (when enabled)
- hard timeout, response size cap, redirect cap, per-run request budget
- per-host serialisation with a minimum interval; global concurrency cap
- 401 / 403 / 429 are reported as blocked and never retried or worked around
- hosts under *.kai-fixture.invalid are served from bundled sample pages and
  never touch the network; results are flagged is_fixture
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .config import PKG, Settings
from .models import now_iso
from .observability import ActivityLog
from .store import Store

USER_AGENT = "KAI-shopping-agent/0.1 (personal shopping assistant; honors robots.txt)"
ROBOTS_TOKEN = "KAI-shopping-agent"
FIXTURE_HOST_SUFFIX = ".kai-fixture.invalid"
MAX_REDIRECTS = 5
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", re.I)

Resolver = Callable[[str], Awaitable[list[str]]]


def is_fixture_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host.endswith(FIXTURE_HOST_SUFFIX)


@dataclass
class FetchBudget:
    max_requests: int
    used: int = 0

    def take(self) -> bool:
        if self.used >= self.max_requests:
            return False
        self.used += 1
        return True


@dataclass
class FetchResult:
    url: str
    final_url: str
    ok: bool = False
    status: int | None = None
    text: str = ""
    content_type: str | None = None
    error: str | None = None
    blocked_reason: str | None = None
    from_cache: bool = False
    is_fixture: bool = False
    truncated: bool = False
    hops: list[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=now_iso)


class SafeFetcher:
    def __init__(self, settings: Settings, store: Store, log: ActivityLog,
                 transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver | None = None):
        self.settings = settings
        self.store = store
        self.log = log
        self._transport = transport
        self._resolver = resolver
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sem: asyncio.Semaphore | None = None
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._robots: dict[str, tuple[RobotFileParser | None, float]] = {}
        self._fixture_index: dict[str, dict] = {}
        idx = PKG / "fixtures" / "pages" / "index.json"
        if idx.exists():
            self._fixture_index = json.loads(idx.read_text(encoding="utf-8"))

    # ---- loop-bound resources -------------------------------------------
    def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(self.settings.fetch_timeout_s, connect=5.0),
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8",
                         "Accept": "text/html,application/xhtml+xml,application/json;q=0.9"},
            )
            self._sem = asyncio.Semaphore(self.settings.fetch_concurrency)
            self._host_locks = {}

    # ---- safety checks ---------------------------------------------------
    async def _host_check(self, host: str) -> str | None:
        """None when the host resolves only to public addresses; otherwise the reason it is refused."""
        try:
            addrs = [str(ipaddress.ip_address(host))]
        except ValueError:
            if host in {"localhost"} or host.endswith((".local", ".internal", ".localhost")):
                return "non_public_address"
            try:
                if self._resolver:
                    addrs = await self._resolver(host)
                else:
                    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
                    addrs = [i[4][0] for i in infos]
            except Exception:
                return "dns_failure"
        if not addrs:
            return "dns_failure"
        for a in addrs:
            ip = ipaddress.ip_address(a.split("%")[0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                    or ip.is_multicast or ip.is_unspecified):
                return "non_public_address"
        return None

    async def _robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if cached and cached[1] > time.time():
            rp = cached[0]
        else:
            rp = None
            try:
                assert self._client is not None
                r = await self._client.get(origin + "/robots.txt")
                if r.status_code == 200 and len(r.content) < 500_000:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
                elif r.status_code >= 500:
                    # RFC 9309: robots.txt unreachable => assume everything is disallowed
                    rp = RobotFileParser()
                    rp.parse(["User-agent: *", "Disallow: /"])
                # 4xx => the site publishes no robots policy
            except Exception:
                rp = None  # the page request itself will fail the same way and be reported
            self._robots[origin] = (rp, time.time() + 3600)
        if rp is None:
            return True
        return rp.can_fetch(ROBOTS_TOKEN, url)

    # ---- main entry ------------------------------------------------------
    async def fetch(self, url: str, budget: FetchBudget | None = None) -> FetchResult:
        self._bind()
        res = FetchResult(url=url, final_url=url)
        current = url

        cached = self.store.cache_get("fetch:" + url)
        if cached:
            data, _stored = cached
            return FetchResult(**{**data, "from_cache": True})

        for _hop in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                res.blocked_reason = "unsupported_scheme"
                res.error = f"Refusing non-http(s) URL: {current[:80]}"
                return res
            host = parts.hostname.lower()

            if host.endswith(FIXTURE_HOST_SUFFIX):
                return self._serve_fixture(url, current, res.hops)

            refused = await self._host_check(host)
            if refused == "dns_failure":
                res.error = f"Could not resolve host {host}; not fetched."
                return res
            if refused:
                res.blocked_reason = refused
                res.error = f"Host {host} is not a public address; not fetched."
                return res
            if self.settings.respect_robots and not await self._robots_allows(current):
                res.blocked_reason = "robots_disallow"
                res.error = f"robots.txt for {host} disallows this path; not fetched."
                return res
            if budget is not None and not budget.take():
                res.blocked_reason = "fetch_budget_exhausted"
                res.error = "Per-run storefront request budget exhausted."
                return res

            try:
                status, headers, raw, truncated = await self._get(current, host)
            except httpx.TimeoutException:
                res.error = f"Timed out after {self.settings.fetch_timeout_s}s"
                return res
            except httpx.HTTPError as e:
                res.error = f"Network error: {type(e).__name__}: {e}"[:300]
                return res

            res.status = status
            res.final_url = current
            if status in (301, 302, 303, 307, 308) and headers.get("location"):
                nxt = urljoin(current, headers["location"])
                res.hops.append(nxt)
                current = nxt
                continue
            if status in (401, 403, 429):
                res.blocked_reason = f"http_{status}"
                res.error = f"Store returned HTTP {status}; access not permitted. Not retried or bypassed."
                return res
            if status >= 400:
                res.error = f"HTTP {status}"
                return res

            ctype = (headers.get("content-type") or "").lower()
            res.content_type = ctype
            if ctype and not any(t in ctype for t in ("html", "json", "xml", "text/plain")):
                res.error = f"Unsupported content type: {ctype[:60]}"
                return res
            res.text = _decode(raw, ctype)
            res.truncated = truncated
            res.ok = True
            self.store.cache_set("fetch:" + url, {
                "url": res.url, "final_url": res.final_url, "ok": True, "status": res.status, "text": res.text,
                "content_type": res.content_type, "hops": res.hops, "truncated": res.truncated,
                "fetched_at": res.fetched_at,
            }, ttl_s=900)
            return res

        res.error = f"Too many redirects (>{MAX_REDIRECTS})"
        return res

    async def _get(self, url: str, host: str) -> tuple[int, httpx.Headers, bytes, bool]:
        assert self._client is not None and self._sem is not None
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with self._sem, lock:
            wait = self.settings.fetch_min_interval_s - (time.monotonic() - self._host_last.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._client.stream("GET", url) as r:
                    chunks: list[bytes] = []
                    size = 0
                    truncated = False
                    if r.status_code < 300:
                        async for chunk in r.aiter_bytes():
                            size += len(chunk)
                            chunks.append(chunk)
                            if size >= self.settings.fetch_max_bytes:
                                truncated = True
                                break
                    return r.status_code, r.headers, b"".join(chunks), truncated
            finally:
                self._host_last[host] = time.monotonic()

    def _serve_fixture(self, original: str, current: str, hops: list[str]) -> FetchResult:
        res = FetchResult(url=original, final_url=current, is_fixture=True, hops=hops)
        if not self.settings.allow_fixtures:
            res.blocked_reason = "fixtures_disabled"
            res.error = "Fixture pages are disabled."
            return res
        key = current.split("?")[0].split("#")[0].rstrip("/")
        entry = self._fixture_index.get(key)
        if not entry:
            res.status = 404
            res.error = "HTTP 404 (fixture page not found)"
            return res
        status = int(entry.get("status", 200))
        res.status = status
        if status in (401, 403, 429):
            res.blocked_reason = f"http_{status}"
            res.error = f"Store returned HTTP {status}; access not permitted. Not retried or bypassed."
            return res
        res.text = (PKG / "fixtures" / "pages" / entry["file"]).read_text(encoding="utf-8")
        res.content_type = "text/html; charset=utf-8"
        res.ok = True
        return res

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass


def _decode(raw: bytes, content_type: str) -> str:
    enc = None
    m = re.search(r"charset=([A-Za-z0-9_\-]+)", content_type or "")
    if m:
        enc = m.group(1)
    else:
        m2 = _META_CHARSET.search(raw[:4096])
        if m2:
            enc = m2.group(1).decode("ascii", "ignore")
    for candidate in [enc, "utf-8"]:
        if not candidate:
            continue
        try:
            return raw.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")
