from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.models import Candidate, SourceItem
from youtube_trend_radar.ranking import freshness_score, interest, rank_candidates
from youtube_trend_radar.resolution import cluster_items, is_relevant, resolve_items, should_merge


NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def make_item(
    title: str,
    *,
    provider: str = "official",
    family: str = "official",
    url: str | None = None,
    entity: str | None = "Anthropic",
    hours_old: float = 1,
    item_type: str = "official_announcement",
    authority: str = "official",
    metrics: dict | None = None,
) -> SourceItem:
    published = NOW - timedelta(hours=hours_old)
    return SourceItem(
        provider=provider,
        external_id=f"{provider}:{title}",
        source_family=family,
        item_type=item_type,
        title=title,
        summary=title,
        canonical_url=url or f"https://example.com/{provider}/{title.replace(' ', '-').lower()}",
        published_at=published,
        updated_at=None,
        observed_at=NOW,
        entity=entity,
        authority=authority,
        metrics=metrics or {},
    )


def test_same_entity_and_time_are_not_enough_to_merge(config: AppConfig) -> None:
    left = make_item("Claude Code hooks update")
    right = make_item("Claude Code background tasks update", provider="hacker_news", family="hacker_news", authority="community")
    assert not should_merge(left, right, config)
    assert len(cluster_items([left, right], config)) == 2


def test_shared_release_anchor_merges_compatible_items(config: AppConfig) -> None:
    metrics = {"repo_full_name": "anthropics/claude-code", "release_tag": "v1.2.3"}
    left = make_item("Claude Code v1.2.3 hooks", metrics=metrics)
    right = make_item(
        "Claude Code v1.2.3 hooks release",
        provider="hacker_news",
        family="hacker_news",
        authority="community",
        metrics=metrics,
    )
    assert should_merge(left, right, config)


def test_exact_canonical_target_merges(config: AppConfig) -> None:
    url = "https://example.com/release?utm_source=news"
    left = make_item("Official release", url=url)
    right = make_item("Show HN discussion", provider="hacker_news", family="hacker_news", url="https://example.com/release")
    assert should_merge(left, right, config)


def test_relevance_requires_watched_entity_or_both_term_families(config: AppConfig) -> None:
    watched = make_item("Claude consumer feature")
    unknown = make_item("Neural frobnicator", entity=None, authority="community")
    relevant = make_item("Open source AI coding SDK", entity=None, authority="community")
    assert is_relevant(watched, config)
    assert not is_relevant(unknown, config)
    assert is_relevant(relevant, config)


def test_short_ai_term_does_not_match_inside_unrelated_words(config: AppConfig) -> None:
    item = make_item(
        "A walkable ASCII cyberpunk city in one HTML file",
        entity=None,
        authority="community",
        url="https://github.com/example/ascii-city",
    )
    assert not is_relevant(item, config)


def test_generic_company_news_needs_a_product_or_developer_anchor(config: AppConfig) -> None:
    item = make_item(
        "The Hugging Face hack could indicate cultural issues at OpenAI",
        entity="OpenAI",
        authority="community",
        provider="hacker_news",
        family="hacker_news",
    )
    assert not is_relevant(item, config)


def test_explicit_repository_owner_wins_over_summary_alias(config: AppConfig) -> None:
    item = make_item(
        "openai/codex 0.152.0",
        entity="openai",
        metrics={"repo_full_name": "openai/codex", "release_tag": "v0.152.0"},
    )
    item.summary = "Adds support for several MCP servers"
    resolve_items([item], config)
    assert item.entity == "OpenAI"


def test_freshness_has_configured_half_life(config: AppConfig) -> None:
    item = make_item("Codex release", entity="OpenAI", hours_old=48)
    candidate = Candidate("x", item.title, item.entity, item.published_at, [item], ["official"])
    assert round(freshness_score(candidate, config, NOW), 6) == 50.0


def test_interest_threshold_boundaries_are_configuration_driven(config: AppConfig) -> None:
    item = make_item(
        "Show HN: AI coding tool",
        provider="hacker_news",
        family="hacker_news",
        entity=None,
        authority="community",
        metrics={"points": 99, "comments": 0},
    )
    candidate = Candidate("x", item.title, None, item.published_at, [item], ["hacker_news"])
    assert interest(candidate, config.ranking.interest, NOW)[:2] == ("moderate", 60)
    item.metrics["points"] = 100
    assert interest(candidate, config.ranking.interest, NOW)[:2] == ("strong", 100)
    tuned = replace(config.ranking.interest, strong_hn_points=200, moderate_hn_points=150)
    assert interest(candidate, tuned, NOW)[:2] == ("early/limited", 25)


def test_missing_observed_growth_is_not_treated_as_zero(config: AppConfig) -> None:
    item = make_item(
        "openai/new-agent: AI coding agent",
        provider="github_explore",
        family="github",
        entity="OpenAI",
        item_type="github_exploratory_repository",
        authority="community",
        metrics={"stars": 10, "observed_growth": {"available": False, "metrics": {"stars": {"delta": 0}}}},
    )
    candidate = Candidate("x", item.title, item.entity, item.published_at, [item], ["github"])
    result = interest(candidate, config.ranking.interest, NOW)
    assert result[3]["github_observed_star_delta"] is None


def test_youtube_never_changes_discovery_priority(config: AppConfig) -> None:
    item = make_item("Codex agent release", entity="OpenAI")
    candidate = Candidate("x", item.title, item.entity, item.published_at, [item], ["official"])
    ranked = rank_candidates([candidate], config, NOW)
    before = ranked[0].discovery_priority
    ranked[0].youtube = {"videos": [{"views": 99_000_000}]}
    assert rank_candidates(ranked, config, NOW)[0].discovery_priority == before
