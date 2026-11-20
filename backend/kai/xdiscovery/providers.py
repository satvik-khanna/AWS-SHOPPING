"""X discovery providers.

All access is through official or user-authorised channels:
- XOfficialAPIProvider      official recent-search API with a bearer token
- IndexedWebSearchProvider  a supported search API (Brave) over publicly indexed posts
- ManualPostImportProvider  posts the user pastes in (optionally resolved via the official API)
- FixtureProvider           bundled sample posts, always labeled as fixtures

There is no scraping of x.com, no login automation, and no handling that
works around 401 / 403 / 429: those are recorded as failures and the
provider stops."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import PKG, Settings
from ..models import ExternalLink, ProviderName, ProviderStatus, XQuery, now_iso
from ..observability import ActivityLog
from ..store import Store
from ..textutil import find_urls, nfkc
from ..vocab import CATEGORIES
from .query_engine import to_indexed_query

_STATUS_URL = re.compile(r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)", re.I)
_X_HOSTS = ("x.com", "twitter.com")  # t.co is a shortener and may point at a store, so it is kept


def parse_status_url(url: str) -> tuple[str, str] | None:
    m = _STATUS_URL.search(url or "")
    return (m.group(1), m.group(2)) if m else None


@dataclass
class RawPost:
    provider: ProviderName
    url: str
    text: str
    post_id: str | None = None
    author_handle: str | None = None
    author_name: str | None = None
    created_at: str | None = None
    lang: str | None = None
    links: list[ExternalLink] = field(default_factory=list)
    media_urls: list[str] = field(default_factory=list)
    query: str | None = None
    is_fixture: bool = False
    partial_text: bool = False
    from_cache: bool = False
    coverage_note: str | None = None
    retrieved_at: str = field(default_factory=now_iso)
    fixture_translation: str | None = None
    category_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["provider"] = self.provider.value if isinstance(self.provider, ProviderName) else self.provider
        d["links"] = [l.to_json() for l in self.links]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawPost":
        d = dict(d)
        d["provider"] = ProviderName(d["provider"])
        d["links"] = [ExternalLink.model_validate(l) for l in d.get("links") or []]
        return cls(**d)


def _is_x_host(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _X_HOSTS)


# ==========================================================================
# Official API
# ==========================================================================


class XOfficialAPIProvider:
    name = ProviderName.X_OFFICIAL_API
    coverage = ("Official X API v2 recent search: public posts from roughly the last 7 days that match the "
                "query. Bounded by the configured result count and spend ceiling, so it is a sample, not "
                "every matching post.")

    def __init__(self, settings: Settings, store: Store, log: ActivityLog,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.store = store
        self.log = log
        self._transport = transport

    def configured(self) -> bool:
        return bool(self.settings.x_bearer_token)

    def spent(self) -> float:
        return self.store.spend("x_api")

    def remaining_budget(self) -> float:
        return max(0.0, self.settings.x_max_spend_usd - self.spent())

    def _worst_case_cost(self, n: int) -> float:
        return n * (self.settings.x_cost_per_post_usd + self.settings.x_cost_per_user_usd)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(15.0, connect=5.0),
                                 headers={"Authorization": f"Bearer {self.settings.x_bearer_token}",
                                          "User-Agent": "KAI-shopping-agent/0.1"})

    async def search(self, queries: list[XQuery], run_id: str | None = None) -> tuple[list[RawPost], ProviderStatus]:
        st = ProviderStatus(provider=self.name, state="unconfigured", coverage=self.coverage)
        if not self.configured():
            st.error = "X_BEARER_TOKEN is not set, so no official X API request was made."
            return [], st

        posts: list[RawPost] = []
        max_results = max(10, min(100, self.settings.x_max_results_per_query))
        st.state = "empty"
        all_cached = True
        async with self._client() as client:
            for q in queries[: self.settings.x_max_queries_per_refresh]:
                if not q.valid:
                    self.log.log("x_search", f"Skipped invalid query: {q.text} ({'; '.join(q.errors)})", "warn", run_id)
                    continue
                cache_key = f"x_recent:{max_results}:{q.text}"
                cached = self.store.cache_get(cache_key)
                if cached:
                    payload, stored_at = cached
                    batch = [RawPost.from_dict(p) for p in payload]
                    for p in batch:
                        p.from_cache = True
                    posts.extend(batch)
                    st.queries_run += 1
                    st.retrieved_at = st.retrieved_at or stored_at
                    self.log.log("x_search", f"X API cache hit ({len(batch)} posts, retrieved {stored_at}): {q.text}",
                                 "info", run_id, {"query": q.text, "cached": True})
                    continue

                worst = self._worst_case_cost(max_results)
                if self.spent() + worst > self.settings.x_max_spend_usd:
                    st.state = "budget_exhausted" if not posts else st.state
                    st.error = (f"X API spend ceiling reached: ${self.spent():.3f} spent of "
                                f"${self.settings.x_max_spend_usd:.2f}; next query could cost up to ${worst:.3f}. "
                                "Raise X_API_MAX_SPEND_USD to continue.")
                    self.log.log("x_search", st.error, "warn", run_id)
                    break

                all_cached = False
                try:
                    r = await client.get(f"{self.settings.x_api_base}/2/tweets/search/recent", params={
                        "query": q.text, "max_results": str(max_results), "sort_order": "recency",
                        "tweet.fields": "created_at,lang,entities,author_id,attachments,referenced_tweets,note_tweet",
                        "expansions": "author_id,attachments.media_keys",
                        "user.fields": "username,name,url,description,entities",
                        "media.fields": "type,url,preview_image_url",
                    })
                except httpx.HTTPError as e:
                    st.state, st.error = "error", f"Network error calling X API: {type(e).__name__}: {e}"[:300]
                    self.log.log("x_search", st.error, "error", run_id, {"query": q.text})
                    break

                st.queries_run += 1
                st.http_status = r.status_code
                if r.status_code in (401, 403, 429):
                    st.state = {401: "unauthorized", 403: "forbidden", 429: "rate_limited"}[r.status_code]
                    detail = _x_error_detail(r)
                    reset = r.headers.get("x-rate-limit-reset")
                    st.error = {
                        401: f"X API returned 401 Unauthorized: the bearer token was rejected. {detail}",
                        403: f"X API returned 403 Forbidden: this app/token is not permitted to use recent search. {detail}",
                        429: f"X API returned 429 Too Many Requests{f' (limit resets at epoch {reset})' if reset else ''}. {detail}",
                    }[r.status_code].strip()
                    self.log.log("x_search", st.error + " Not retried.", "error", run_id,
                                 {"query": q.text, "httpStatus": r.status_code})
                    break
                if r.status_code == 400:
                    self.log.log("x_search", f"X API rejected query (400): {q.text} - {_x_error_detail(r)}", "warn",
                                 run_id, {"query": q.text})
                    st.error = f"X API rejected a query as invalid: {_x_error_detail(r)}"
                    continue
                if r.status_code >= 400:
                    st.state, st.error = "error", f"X API returned HTTP {r.status_code}: {_x_error_detail(r)}"
                    self.log.log("x_search", st.error, "error", run_id, {"query": q.text})
                    break

                body = r.json()
                batch, n_users = self._parse(body, q.text)
                n_posts = len(body.get("data") or [])
                cost = n_posts * self.settings.x_cost_per_post_usd + n_users * self.settings.x_cost_per_user_usd
                st.cost_usd_estimate += cost
                self.log.usage("x_api", "search_recent", n_posts, "posts", cost,
                               {"query": q.text, "users": n_users, "estimated": True})
                self.store.cache_set(cache_key, [p.to_dict() for p in batch], self.settings.x_cache_ttl_s)
                posts.extend(batch)
                st.retrieved_at = now_iso()
                self.log.log("x_search", f"X API returned {n_posts} post(s) ({len(batch)} kept after dropping reposts) "
                                         f"for: {q.text} (est. ${cost:.3f})", "ok", run_id,
                             {"query": q.text, "posts": n_posts, "estCostUsd": round(cost, 4)})

        st.posts_returned = len(posts)
        st.from_cache = bool(posts) and all_cached
        if posts and st.state in ("empty", "budget_exhausted"):
            st.state = "ok"
        return posts, st

    def _parse(self, body: dict[str, Any], query: str) -> tuple[list[RawPost], int]:
        users = {u["id"]: u for u in (body.get("includes") or {}).get("users") or []}
        media = {m["media_key"]: m for m in (body.get("includes") or {}).get("media") or []}
        out: list[RawPost] = []
        for t in body.get("data") or []:
            if any(ref.get("type") == "retweeted" for ref in t.get("referenced_tweets") or []):
                continue
            user = users.get(t.get("author_id"), {})
            handle = user.get("username")
            note = t.get("note_tweet") or {}
            text = note.get("text") or t.get("text") or ""
            entities = (note.get("entities") or t.get("entities") or {})
            links: list[ExternalLink] = []
            for u in entities.get("urls") or []:
                target = u.get("unwound_url") or u.get("expanded_url") or u.get("url")
                if not target or u.get("media_key") or _is_x_host(target):
                    continue  # media attachments and links back to X are not store links
                links.append(ExternalLink(url=u.get("url") or target, expanded_url=target,
                                          display_url=u.get("display_url"), source="api_entities"))
            # Profile website links are useful when the post says "link in profile".
            for u in ((user.get("entities") or {}).get("url") or {}).get("urls") or []:
                target = u.get("expanded_url")
                if target and not _is_x_host(target) and all(l.target != target for l in links):
                    links.append(ExternalLink(url=u.get("url") or target, expanded_url=target,
                                              display_url=u.get("display_url"), source="api_profile"))
            media_urls = []
            for key in (t.get("attachments") or {}).get("media_keys") or []:
                m = media.get(key) or {}
                if m.get("url") or m.get("preview_image_url"):
                    media_urls.append(m.get("url") or m.get("preview_image_url"))
            out.append(RawPost(
                provider=self.name, post_id=t.get("id"),
                url=f"https://x.com/{handle or 'i'}/status/{t.get('id')}" if handle else f"https://x.com/i/web/status/{t.get('id')}",
                author_handle=handle, author_name=user.get("name"), text=text, created_at=t.get("created_at"),
                lang=t.get("lang"), links=links, media_urls=media_urls, query=query, coverage_note=self.coverage,
            ))
        return out, len(users)

    async def lookup(self, post_id: str, run_id: str | None = None) -> tuple[RawPost | None, str | None]:
        """Resolve one post by id (used by manual import when a token is available)."""
        if not self.configured():
            return None, "X_BEARER_TOKEN is not set"
        worst = self._worst_case_cost(1)
        if self.spent() + worst > self.settings.x_max_spend_usd:
            return None, "X API spend ceiling reached"
        try:
            async with self._client() as client:
                r = await client.get(f"{self.settings.x_api_base}/2/tweets", params={
                    "ids": post_id,
                    "tweet.fields": "created_at,lang,entities,author_id,attachments,referenced_tweets,note_tweet",
                    "expansions": "author_id,attachments.media_keys",
                    "user.fields": "username,name,url,description,entities",
                    "media.fields": "type,url,preview_image_url",
                })
        except httpx.HTTPError as e:
            return None, f"Network error calling X API: {type(e).__name__}"
        if r.status_code >= 400:
            msg = f"X API returned HTTP {r.status_code}: {_x_error_detail(r)}"
            self.log.log("x_search", f"Post lookup failed. {msg} Not retried.", "error", run_id)
            return None, msg
        posts, n_users = self._parse(r.json(), f"ids:{post_id}")
        cost = len(posts) * self.settings.x_cost_per_post_usd + n_users * self.settings.x_cost_per_user_usd
        self.log.usage("x_api", "tweet_lookup", len(posts), "posts", cost, {"estimated": True})
        return (posts[0], None) if posts else (None, "Post not found or not accessible")


def _x_error_detail(r: httpx.Response) -> str:
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        return r.text[:160]
    if isinstance(body, dict):
        if body.get("errors"):
            e = body["errors"][0]
            return str(e.get("message") or e.get("detail") or e)[:200]
        return str(body.get("detail") or body.get("title") or "")[:200]
    return ""


# ==========================================================================
# Indexed web search (Brave Search API)
# ==========================================================================


class IndexedWebSearchProvider:
    name = ProviderName.INDEXED_WEB_SEARCH
    coverage = ("Search-engine index of public x.com posts (Brave Search API). Only posts the engine has "
                "indexed; text is a snippet that may be truncated or stale; shortened links are not "
                "expanded; post time may be missing.")

    def __init__(self, settings: Settings, store: Store, log: ActivityLog,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.store = store
        self.log = log
        self._transport = transport

    def configured(self) -> bool:
        return bool(self.settings.brave_api_key)

    async def search(self, queries: list[XQuery], run_id: str | None = None) -> tuple[list[RawPost], ProviderStatus]:
        st = ProviderStatus(provider=self.name, state="unconfigured", coverage=self.coverage)
        if not self.configured():
            st.error = "BRAVE_SEARCH_API_KEY is not set, so no indexed web search was made."
            return [], st
        st.state = "empty"
        posts: list[RawPost] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            for q in queries[: self.settings.x_max_queries_per_refresh]:
                web_q = to_indexed_query(q.text)
                try:
                    r = await client.get(f"{self.settings.brave_api_base}/res/v1/web/search",
                                         params={"q": web_q, "count": "10", "freshness": "pm"},
                                         headers={"X-Subscription-Token": self.settings.brave_api_key or "",
                                                  "Accept": "application/json"})
                except httpx.HTTPError as e:
                    st.state, st.error = "error", f"Network error calling search API: {type(e).__name__}: {e}"[:300]
                    self.log.log("x_search", st.error, "error", run_id)
                    break
                st.queries_run += 1
                st.http_status = r.status_code
                if r.status_code in (401, 403, 429):
                    st.state = {401: "unauthorized", 403: "forbidden", 429: "rate_limited"}[r.status_code]
                    st.error = f"Search API returned HTTP {r.status_code}. Not retried."
                    self.log.log("x_search", st.error, "error", run_id, {"query": web_q})
                    break
                if r.status_code >= 400:
                    st.state, st.error = "error", f"Search API returned HTTP {r.status_code}: {r.text[:160]}"
                    self.log.log("x_search", st.error, "error", run_id, {"query": web_q})
                    break
                self.log.usage("web_search", "brave_web_search", 1, "queries", None, {"query": web_q})
                results = ((r.json().get("web") or {}).get("results")) or []
                kept = 0
                for item in results:
                    parsed = parse_status_url(item.get("url") or "")
                    if not parsed or parsed[1] in seen:
                        continue
                    seen.add(parsed[1])
                    handle, pid = parsed
                    text = _snippet_text(item.get("title") or "", item.get("description") or "")
                    if not text:
                        continue
                    links = [ExternalLink(url=u, source="search_snippet") for u in find_urls(text) if not _is_x_host(u)]
                    posts.append(RawPost(
                        provider=self.name, post_id=pid, url=f"https://x.com/{handle}/status/{pid}",
                        author_handle=handle, text=text, created_at=item.get("page_age"), links=links, query=q.text,
                        partial_text=True, coverage_note=self.coverage,
                    ))
                    kept += 1
                st.retrieved_at = now_iso()
                self.log.log("x_search", f"Indexed search returned {len(results)} result(s), {kept} x.com post(s): {web_q}",
                             "ok", run_id, {"query": web_q, "posts": kept})
        st.posts_returned = len(posts)
        if posts:
            st.state = "ok"
        return posts, st


def _snippet_text(title: str, description: str) -> str:
    """Search results title posts as: Name on X: "text" / X. Prefer the longer of
    the quoted title text and the description snippet."""
    quoted = ""
    m = re.search(r'on X: ["“](.+?)["”]?\s*(/ X)?$', title, re.S) or re.search(r'[:：]\s*["「“](.+?)["」”]?\s*/ X$', title, re.S)
    if m:
        quoted = m.group(1)
    desc = re.sub(r"<[^>]+>", "", description)
    return (desc if len(desc) >= len(quoted) else quoted).strip()


# ==========================================================================
# Manual import
# ==========================================================================


class ManualPostImportProvider:
    name = ProviderName.MANUAL_IMPORT
    coverage = "Posts supplied by the user. Text and links are as provided; KAI has not checked them against X unless noted."

    def __init__(self, store: Store, log: ActivityLog, official: XOfficialAPIProvider):
        self.store = store
        self.log = log
        self.official = official

    def configured(self) -> bool:
        return True

    async def import_post(self, url: str, text: str | None = None, author_handle: str | None = None,
                          links: list[str] | None = None, posted_at: str | None = None,
                          run_id: str | None = None) -> tuple[RawPost | None, str | None]:
        parsed = parse_status_url(url)
        if not parsed:
            return None, "That is not an x.com / twitter.com post URL (expected https://x.com/<handle>/status/<id>)."
        handle, pid = parsed
        text = (text or "").strip()
        post: RawPost | None = None
        note = self.coverage
        if not text:
            post, err = await self.official.lookup(pid, run_id)
            if post is None:
                return None, (f"No post text was supplied and the post could not be resolved through the official "
                              f"X API ({err}). Paste the post text to import it.")
            post.provider = self.name
            note = "Supplied by URL; text and links resolved through the official X API."
            post.coverage_note = note
        else:
            ext = [ExternalLink(url=u, source="user_supplied") for u in (links or []) if u.strip().startswith("http")]
            for u in find_urls(text):
                if not _is_x_host(u) and all(l.url != u for l in ext):
                    ext.append(ExternalLink(url=u, source="text"))
            post = RawPost(provider=self.name, post_id=pid, url=f"https://x.com/{handle}/status/{pid}",
                           author_handle=(author_handle or handle).lstrip("@"), text=text, created_at=posted_at,
                           links=ext, coverage_note=note)
        self.store.put("manual_post", pid, post.to_dict())
        self.log.log("x_import", f"Imported post {post.url} ({'text supplied by user' if text else 'resolved via X API'})",
                     "ok", run_id)
        return post, None

    async def search(self, queries: list[XQuery], run_id: str | None = None, category: str | None = None
                     ) -> tuple[list[RawPost], ProviderStatus]:
        posts = [RawPost.from_dict(d) for d in self.store.list("manual_post")]
        if category and category in CATEGORIES:
            terms = [t.lower() for t in CATEGORIES[category]["ja"] + CATEGORIES[category]["en"]]
            posts = [p for p in posts if any(t in nfkc(p.text).lower() for t in terms)]
        st = ProviderStatus(provider=self.name, state="ok" if posts else "empty", coverage=self.coverage,
                            posts_returned=len(posts), retrieved_at=now_iso() if posts else None)
        return posts, st


# ==========================================================================
# Fixtures
# ==========================================================================


class FixtureProvider:
    name = ProviderName.FIXTURE
    coverage = ("SAMPLE DATA. Bundled fixture posts written for offline demos and tests. They are not real X "
                "posts, the accounts and stores do not exist, and nothing here reflects current availability.")

    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        return self.settings.allow_fixtures

    async def search(self, queries: list[XQuery], run_id: str | None = None, category: str | None = None
                     ) -> tuple[list[RawPost], ProviderStatus]:
        st = ProviderStatus(provider=self.name, state="unconfigured", coverage=self.coverage, is_fixture=True)
        if not self.configured():
            st.error = "Fixtures are disabled (KAI_ALLOW_FIXTURES=false)."
            return [], st
        data = json.loads((PKG / "fixtures" / "x_posts.json").read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc)
        posts: list[RawPost] = []
        for item in data["posts"]:
            if category and item.get("category") != category:
                continue
            text = _render_dates(item["text"], now)
            handle = item["handle"]
            posts.append(RawPost(
                # Fixture posts live on a reserved .invalid host so they can never be mistaken
                # for, or link to, a real x.com account.
                provider=self.name, post_id=item["id"],
                url=f"https://x.kai-fixture.invalid/{handle}/status/{item['id']}",
                author_handle=handle, author_name=item.get("name"), text=text,
                created_at=(now - timedelta(hours=item.get("hoursAgo", 24))).isoformat(timespec="seconds"),
                lang="ja",
                links=[ExternalLink(url=u, expanded_url=u, source="fixture") for u in item.get("links", [])],
                media_urls=item.get("media", []), is_fixture=True, coverage_note=self.coverage,
                query=None,  # fixtures are not the result of any search query
                fixture_translation=_render_dates(item.get("translation") or "", now) or None,
                category_hint=item.get("category"),
            ))
        st.state = "ok" if posts else "empty"
        st.posts_returned = len(posts)
        st.retrieved_at = now_iso()
        return posts, st


def _render_dates(text: str, now: datetime) -> str:
    """Fixture texts use {{md:+3}} (-> 11/23) and {{jp:+3}} (-> 11月23日) so sample
    deadlines stay in the near future relative to today."""
    def repl(m: re.Match[str]) -> str:
        d = now + timedelta(days=int(m.group(2)))
        return f"{d.month}/{d.day}" if m.group(1) == "md" else f"{d.month}月{d.day}日"
    return re.sub(r"\{\{(md|jp):([+-]?\d+)\}\}", repl, text)
