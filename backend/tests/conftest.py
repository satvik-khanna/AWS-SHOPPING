"""Shared test helpers. No test touches the real network: every outbound HTTP
call goes through an httpx.MockTransport, and DNS is stubbed."""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from kai.app import Kai, build
from kai.config import Settings

REQUIRED_CASE = "Find me Japanese artisan keycaps announced recently on X, under $120 delivered to California."
PUBLIC_IP = "93.184.216.34"


async def public_resolver(host: str) -> list[str]:
    return [PUBLIC_IP]


class Net:
    """Records every outbound request and routes it to a handler."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response] | None = None):
        self.requests: list[httpx.Request] = []
        self.handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler is None:
            return httpx.Response(599, text="unexpected network call in an offline test")
        return self.handler(request)

    def to(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]

    def paths(self, host: str) -> list[str]:
        return [r.url.path for r in self.to(host)]


@pytest.fixture
def make_kai(tmp_path):
    made: list[Kai] = []

    def _make(handler=None, resolver=public_resolver, cognee_module=None, **overrides) -> tuple[Kai, Net]:
        net = Net(handler)
        base = dict(data_dir=tmp_path, fx_live=False, fetch_min_interval_s=0.0)
        base.update(overrides)
        k = build(Settings(**base), transport=httpx.MockTransport(net), resolver=resolver,
                  cognee_module=cognee_module, db_path=str(tmp_path / f"kai{len(made)}.sqlite3"))
        made.append(k)
        return k, net

    return _make


def html_product(name: str, price: int, availability: str = "InStock", extra: str = "", store: str = "テスト工房") -> str:
    ld = {"@context": "https://schema.org", "@type": "Product", "name": name,
          "description": f"{name} ハンドメイド マット仕上げ",
          "offers": {"@type": "Offer", "price": price, "priceCurrency": "JPY",
                     "availability": f"https://schema.org/{availability}", "seller": {"@type": "Organization", "name": store}}}
    return (f"<html><head><title>{name}</title><script type=\"application/ld+json\">{json.dumps(ld, ensure_ascii=False)}"
            f"</script></head><body><h1>{name}</h1><p>¥{price:,}</p>{extra}</body></html>")


def html_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=body.encode("utf-8"), headers={"content-type": "text/html; charset=utf-8"})


X_SEARCH_BODY = {
    "data": [
        {"id": "1900000000000000001", "author_id": "u1", "created_at": "2026-11-19T10:00:00.000Z", "lang": "ja",
         "text": "【新作】アルチザンキーキャップ「蒼天」販売開始しました！ひとつずつ手作りのハンドメイド、マット仕上げです。¥7,200 https://t.co/aaa #キーキャップ",
         "entities": {"urls": [{"url": "https://t.co/aaa", "expanded_url": "https://souten-keys.booth.pm/items/555",
                                "display_url": "souten-keys.booth.pm/items/555"}]},
         "attachments": {"media_keys": ["m1"]}},
        {"id": "1900000000000000002", "author_id": "u2", "created_at": "2026-11-19T09:00:00.000Z", "lang": "ja",
         "text": "RT @souten_keys: 【新作】アルチザンキーキャップ「蒼天」販売開始しました！",
         "referenced_tweets": [{"type": "retweeted", "id": "1900000000000000001"}]},
        {"id": "1900000000000000003", "author_id": "u2", "created_at": "2026-11-18T12:00:00.000Z", "lang": "ja",
         "text": "【抽選販売】キーキャップ「霧氷」の抽選受付を開始します。11/25まで。12,000円 https://t.co/bbb",
         "entities": {"urls": [{"url": "https://t.co/bbb", "expanded_url": "https://muhyo.thebase.in/items/777"}]}},
        {"id": "1900000000000000004", "author_id": "u3", "created_at": "2026-11-17T08:00:00.000Z", "lang": "ja",
         "text": "キーキャップ「紅葉」再販します！通販は週末から。写真はこちら https://t.co/ccc",
         "entities": {"urls": [{"url": "https://t.co/ccc", "expanded_url": "https://x.com/momiji_caps/status/1900000000000000004/photo/1",
                                "media_key": "m2"}]}},
    ],
    "includes": {
        "users": [{"id": "u1", "username": "souten_keys", "name": "蒼天工房"},
                  {"id": "u2", "username": "muhyo_resin", "name": "霧氷レジン"},
                  {"id": "u3", "username": "momiji_caps", "name": "もみじ"}],
        "media": [{"media_key": "m1", "type": "photo", "url": "https://pbs.twimg.com/media/sample.jpg"}],
    },
    "meta": {"result_count": 4},
}


def live_handler(x_status: int = 200, x_body: dict | None = None):
    """Simulated X API + two store pages. Used to exercise the live code path."""

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "api.x.com":
            if x_status != 200:
                return httpx.Response(x_status, json={"title": "Denied", "detail": "simulated failure"},
                                      headers={"x-rate-limit-reset": "1795000000"})
            return httpx.Response(200, json=x_body or X_SEARCH_BODY)
        if path == "/robots.txt":
            return httpx.Response(404)
        if host == "souten-keys.booth.pm" and path == "/items/555":
            return html_response(html_product("蒼天 アルチザンキーキャップ", 7200, store="蒼天工房"))
        if host == "muhyo.thebase.in" and path == "/items/777":
            return html_response(html_product("霧氷 キーキャップ", 12000, extra="<p>抽選販売です。抽選受付は11/25まで。</p>", store="霧氷レジン"))
        return httpx.Response(404)

    return handler
