import pytest

from kai.models import MemoryRecall


@pytest.mark.parametrize("text", ["matte, not glossy", "matte and not glossy", "matte not glossy"])
async def test_request_negation_is_used_for_ranking_and_draft_choice(make_kai, text):
    k, _ = make_kai()
    await k.memory.add_preference("I like glossy finishes")
    run = await k.agent.run(f"Find me {text} artisan keycaps under $150 delivered to California")
    assert "matte" in run.request.stated_preferences and "glossy" not in run.request.stated_preferences
    assert run.request.stated_avoidances == ["glossy"]
    likes, avoids = run.request.preference_signals(run.memory)
    assert "glossy" not in likes and "glossy" in avoids
    glossy = [r for r in run.ranked if "glossy" in r.candidate.attributes]
    assert glossy and all(any("you avoid" in reason for reason in r.score.reasons) for r in glossy)
    assert run.draft is None or run.draft.candidate_id not in {r.candidate.id for r in glossy}
    assert "glossy" in (await k.memory.recall("keycaps")).preferred, "one brief must not rewrite lasting taste"


async def test_explicit_preference_keeps_negation_in_memory(make_kai):
    k, _ = make_kai()
    saved = await k.memory.add_preference("I prefer matte and not glossy")
    assert {(p["attribute"], p["polarity"]) for p in saved} == {("matte", "like"), ("glossy", "avoid")}
    request = await k.agent.parse_request("Glossy artisan keycaps please")
    likes, avoids = request.preference_signals(MemoryRecall(backend="fallback", avoided=["glossy"]))
    assert "glossy" in likes and avoids == []


async def test_zero_budget_returns_comparison_instead_of_crashing(make_kai):
    k, _ = make_kai()
    run = await k.agent.run("Artisan keycaps under $0 to California")
    assert run.ranked and (run.draft is None or run.draft.path == "MANUAL_ONLY")
    assert all(r.cost.within_budget == "no" for r in run.ranked if r.cost.total_usd is not None)
