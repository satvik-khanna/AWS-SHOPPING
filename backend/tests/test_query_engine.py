from kai.vocab import ANNOUNCEMENT_TERMS
from kai.xdiscovery.query_engine import build_queries, to_indexed_query, validate_x_query


def test_generates_three_to_six_valid_japanese_queries():
    qs = build_queries("keycaps", preferred_attributes=["handmade", "matte"])
    assert 3 <= len(qs) <= 6
    assert all(q.valid and not q.errors for q in qs)
    assert all("キーキャップ" in q.text for q in qs)
    assert all("lang:ja" in q.text and "-is:retweet" in q.text for q in qs)
    assert len({q.text for q in qs}) == len(qs)
    # the canonical example from the requirement
    assert '"キーキャップ" (新作 OR 限定 OR 再販) lang:ja -is:retweet' in [q.text for q in qs]


def test_queries_cover_separate_intents_and_announcement_vocabulary():
    qs = build_queries("keycaps")
    assert len({q.intent for q in qs}) >= 3
    joined = " ".join(q.text for q in qs)
    for term in ["新作", "販売開始", "予約開始", "抽選販売", "再販", "数量限定", "受注生産", "通販", "入荷", "先着"]:
        assert term in joined, term
        assert term in ANNOUNCEMENT_TERMS


def test_preferences_produce_a_preference_query():
    qs = build_queries("keycaps", preferred_attributes=["handmade", "matte"])
    pref = [q for q in qs if q.source == "preference"]
    assert pref and "マット" in pref[0].text and "ハンドメイド" in pref[0].text


def test_no_popularity_operators():
    for q in build_queries("figures", preferred_attributes=["handmade"]):
        assert "min_faves" not in q.text and "min_retweets" not in q.text


def test_watched_creators_and_terms_are_used():
    watched = [{"handle": "tsukumo_keys", "categories": ["keycaps"], "searchTerms": ["レジンキーキャップ"]}]
    qs = build_queries("keycaps", watched=watched)
    assert 3 <= len(qs) <= 6
    assert any("from:tsukumo_keys" in q.text for q in qs)
    assert any("レジンキーキャップ" in q.text for q in qs)
    assert all(q.valid for q in qs)


def test_other_categories_and_custom_terms():
    for cat in ["figures", "cameras", "handmade", "stationery", "fashion"]:
        qs = build_queries(cat)
        assert 3 <= len(qs) <= 6 and all(q.valid for q in qs)
    qs = build_queries("custom", ja_terms=["香炉"])
    assert 3 <= len(qs) <= 6 and all("香炉" in q.text and q.valid for q in qs)


def test_validator_rejects_bad_syntax():
    assert validate_x_query("") == ["query is empty"]
    assert any("parentheses" in e for e in validate_x_query('"キーキャップ" (新作 OR 限定'))
    assert any("quotes" in e for e in validate_x_query('"キーキャップ (新作)'))
    assert any("limit" in e for e in validate_x_query("キーキャップ " * 80))
    # operators that cannot stand alone on the recent-search endpoint
    assert validate_x_query("lang:ja -is:retweet")
    assert validate_x_query("キーキャップ OR") != []
    assert validate_x_query('"キーキャップ" (新作 OR 限定) lang:ja -is:retweet') == []


def test_indexed_query_drops_x_only_operators():
    q = to_indexed_query('"キーキャップ" (新作 OR 限定 OR 再販) lang:ja has:links -is:retweet')
    assert q.startswith("site:x.com")
    assert "lang:" not in q and "is:retweet" not in q and "has:links" not in q
    assert "キーキャップ" in q and "新作" in q
