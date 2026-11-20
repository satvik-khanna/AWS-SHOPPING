"""Polite stdlib HTTP for live listing search (SHOPPER_LIVE=1 only).

- identifies itself with a plain User-Agent, no browser impersonation, no cookies, no retries
- obeys robots.txt for that User-Agent; robots.txt that cannot be read (403 / network error)
  counts as "do not crawl" for the whole host
- at most 1 request per second across all hosts (robots.txt fetches included)
- the final URL after redirects is checked against robots.txt again
"""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from typing import Callable

USER_AGENT = "aws-shopping-hackathon/0.1 (python-urllib; listing search demo)"
MIN_INTERVAL_S = 1.0


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _urllib_get(url: str, timeout: float) -> tuple[int, str, str]:
    """-> (status, final_url, body). HTTP errors come back as status, network errors raise."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            if r.headers.get("x-amzn-waf-action"):
                return 403, r.geturl(), ""          # bot challenge: treat as refused, never solve it
            return r.status, r.geturl(), body
    except urllib.error.HTTPError as e:
        return e.code, url, ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(f"network: {e}") from e


class PoliteFetcher:
    def __init__(self, get: Callable[[str, float], tuple[int, str, str]] | None = None,
                 min_interval: float = MIN_INTERVAL_S, timeout: float = 10.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.get = get or _urllib_get
        self.min_interval = min_interval
        self.timeout = timeout
        self.sleep, self.clock = sleep, clock
        self._last = -1e9
        self._lock = threading.Lock()
        self._robots: dict[str, urllib.robotparser.RobotFileParser | str] = {}

    def _get(self, url: str) -> tuple[int, str, str]:
        with self._lock:
            wait = self._last + self.min_interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            try:
                return self.get(url, self.timeout)
            finally:
                self._last = self.clock()

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | str:
        p = urllib.parse.urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            try:
                status, _final, body = self._get(host + "/robots.txt")
            except FetchError as e:
                status, body = 0, str(e)
            if status == 200:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(body.splitlines())
                self._robots[host] = rp
            elif status == 404:                      # no robots.txt: everything allowed
                rp = urllib.robotparser.RobotFileParser()
                rp.parse([])
                self._robots[host] = rp
            else:
                self._robots[host] = f"robots.txt unreadable (HTTP {status or 'network error'})"
        return self._robots[host]

    def allowed(self, url: str) -> tuple[bool, str]:
        rp = self._robots_for(url)
        if isinstance(rp, str):
            return False, rp
        ok = rp.can_fetch(USER_AGENT, url)
        return ok, "" if ok else "disallowed by robots.txt"

    def fetch(self, url: str) -> tuple[str, str]:
        """-> (final_url, html). Raises RobotsDisallowed / FetchError."""
        ok, why = self.allowed(url)
        if not ok:
            raise RobotsDisallowed(f"{url}: {why}")
        status, final, body = self._get(url)
        if final != url:
            ok, why = self.allowed(final)
            if not ok:
                raise RobotsDisallowed(f"redirected to {final}: {why}")
        if status != 200 or not body:
            raise FetchError(f"{url}: HTTP {status}")
        return final, body
