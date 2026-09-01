from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.models import Candidate, SourceItem
from youtube_trend_radar.ranking import (
    attach_repository_support,
    candidate_is_eligible,
    eligible_items,
    freshness_score,
    interest,
    partition_community_watch,
    partition_main_list_floor,
    rank_candidates,
)
from youtube_trend_radar.resolution import cluster_items, effective_item_time, is_relevant, resolve_items, should_merge


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


def test_relevance_requires_developer_product_or_both_term_families(config: AppConfig) -> None:
    watched = make_item("Claude Code hooks feature")
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


def test_legal_news_with_model_name_in_url_is_not_channel_relevant(config: AppConfig) -> None:
    item = make_item(
        "Anthropic sued over alleged theft of thousands of songs",
        entity="Anthropic",
        authority="community",
        provider="hacker_news",
        family="hacker_news",
        url="https://news.example/anthropic-ai-train-claude",
    )
    item.summary = ""
    assert not is_relevant(item, config)


def test_broad_official_story_does_not_qualify_from_incidental_summary_products(config: AppConfig) -> None:
    item = make_item(
        "Polimill builds Japan's next-generation public AI infrastructure",
        entity="OpenAI",
        authority="official",
        provider="official",
        family="official",
        item_type="official_announcement",
    )
    item.summary = "Uses GPT models and Codex while accelerating development"
    assert not is_relevant(item, config)


def test_show_hn_can_use_description_to_establish_developer_relevance(config: AppConfig) -> None:
    item = make_item(
        "Show HN: Saccade – live semantic browser truth",
        entity=None,
        authority="community",
        provider="hacker_news",
        family="hacker_news",
        item_type="hacker_news_story",
    )
    item.summary = "An MCP developer tool for AI agents"
    assert is_relevant(item, config)


def test_summary_only_product_mention_does_not_assign_entity(config: AppConfig) -> None:
    item = make_item(
        "Show HN: Fly By – retro biplane flying game",
        entity=None,
        authority="community",
        provider="hacker_news",
        family="hacker_news",
        url="https://example.com/flyby",
    )
    item.summary = "My concept and Gemini Flash doing the work"
    resolve_items([item], config)
    assert item.entity is None
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


def test_old_watched_repository_snapshot_is_support_only(config: AppConfig) -> None:
    item = make_item(
        "openai/codex: coding agent",
        provider="github_watched",
        family="github",
        entity="OpenAI",
        hours_old=24 * 500,
        item_type="github_repository_snapshot",
        authority="community",
        metrics={
            "repo_full_name": "openai/codex",
            "stars": 120_575,
            "observed_growth": {
                "available": True,
                "observation_duration_hours": 0.84,
                "metrics": {"stars": {"initial": 120_559, "current": 120_575, "delta": 16}},
            },
        },
    )
    assert effective_item_time(item) == item.published_at
    assert eligible_items([item], config, NOW) == []


def test_watched_repository_growth_requires_duration_absolute_and_relative_gates(config: AppConfig) -> None:
    item = make_item(
        "openai/codex: coding agent",
        provider="github_watched",
        family="github",
        entity="OpenAI",
        hours_old=24 * 500,
        item_type="github_repository_snapshot",
        authority="community",
        metrics={
            "repo_full_name": "openai/codex",
            "stars": 10_100,
            "observed_growth": {
                "available": True,
                "observation_duration_hours": 25.0,
                "metrics": {"stars": {"initial": 10_000, "current": 10_100, "delta": 100}},
            },
        },
    )
    events = eligible_items([item], config, NOW)
    assert len(events) == 1
    assert events[0].item_type == "github_observed_growth"
    assert events[0].published_at == item.observed_at
    assert events[0].metrics["observed_star_relative_percent"] == 1.0


def test_new_watched_repository_uses_creation_time_as_event(config: AppConfig) -> None:
    item = make_item(
        "openai/new-codex-tool: coding agent",
        provider="github_watched",
        family="github",
        entity="OpenAI",
        hours_old=12,
        item_type="github_repository_snapshot",
        authority="community",
        metrics={"repo_full_name": "openai/new-codex-tool", "stars": 3},
    )
    events = eligible_items([item], config, NOW)
    assert events[0].item_type == "github_new_repository"
    assert effective_item_time(events[0]) == item.published_at


def test_repository_snapshot_supports_release_without_changing_event_time(config: AppConfig) -> None:
    release = make_item(
        "openai/codex 0.152.0",
        provider="github_watched",
        family="github",
        entity="OpenAI",
        hours_old=8,
        item_type="github_release",
        metrics={"repo_full_name": "openai/codex", "release_tag": "v0.152.0"},
    )
    snapshot = make_item(
        "openai/codex: coding agent",
        provider="github_watched",
        family="github",
        entity="OpenAI",
        hours_old=24 * 500,
        item_type="github_repository_snapshot",
        authority="community",
        metrics={"repo_full_name": "openai/codex", "stars": 120_000},
    )
    candidates = cluster_items([release], config)
    original_time = candidates[0].effective_event_time
    attach_repository_support(candidates, [release, snapshot])
    assert snapshot in candidates[0].items
    assert candidates[0].effective_event_time == original_time


def test_weak_hn_only_candidate_is_ineligible_but_release_is_preserved(config: AppConfig) -> None:
    weak = make_item(
        "Show HN: AI coding tool",
        provider="hacker_news",
        family="hacker_news",
        entity=None,
        authority="community",
        metrics={"points": 1, "comments": 0},
    )
    weak_candidate = Candidate("weak", weak.title, None, weak.published_at, [weak], ["hacker_news"])
    assert not candidate_is_eligible(weak_candidate, config)
    weak.metrics["points"] = config.ranking.eligibility.community_hn_min_points
    assert candidate_is_eligible(weak_candidate, config)

    release = make_item("Codex 1.0 released", entity="OpenAI")
    release_candidate = Candidate("release", release.title, release.entity, release.published_at, [release], ["official"])
    assert candidate_is_eligible(release_candidate, config)


def test_relevant_emerging_github_project_remains_eligible(config: AppConfig) -> None:
    project = make_item(
        "org/new-agent: open source AI coding agent",
        provider="github_explore",
        family="github",
        entity=None,
        item_type="github_exploratory_repository",
        authority="community",
        metrics={"stars": config.ranking.eligibility.github_explore_min_stars},
    )
    candidate = Candidate("project", project.title, None, project.published_at, [project], ["github"])
    assert candidate_is_eligible(candidate, config)


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


def test_non_latin_exploratory_topic_moves_to_community_watch(config: AppConfig) -> None:
    item = make_item(
        "org/AgentGuide: 智能开发工作流实践指南和自动化教程",
        provider="github_explore",
        family="github",
        entity=None,
        item_type="github_exploratory_repository",
        authority="community",
        metrics={"stars": 50},
    )
    item.summary = "面向开发者的智能代理工具、真实任务和自动化工作流实践指南"
    candidate = Candidate("language", item.title, None, item.published_at, [item], ["github"])
    candidate.discovery_priority = 72.5

    main, watch = partition_community_watch([candidate], config)

    assert main == []
    assert watch == [candidate]
    assert candidate.presentation_gate["gate"] == "language"
    assert candidate.presentation_gate["measurements"]["latin_letter_ratio"] < 0.6
    assert candidate.discovery_priority == 72.5


def test_non_latin_topic_with_independent_latin_support_remains_main(config: AppConfig) -> None:
    project = make_item(
        "org/AgentGuide: 智能开发工作流实践指南和自动化教程",
        provider="github_explore",
        family="github",
        entity=None,
        item_type="github_exploratory_repository",
        authority="community",
        metrics={"stars": 50},
    )
    project.summary = "面向开发者的智能代理工具、真实任务和自动化工作流实践指南"
    discussion = make_item(
        "Show HN: AgentGuide for automating AI developer workflows",
        provider="hacker_news",
        family="hacker_news",
        entity=None,
        authority="community",
        metrics={"points": 30, "comments": 5},
    )
    discussion.summary = "An English overview of the tool and its coding-agent workflow"
    candidate = Candidate(
        "supported-language",
        project.title,
        None,
        project.published_at,
        [project, discussion],
        ["github", "hacker_news"],
    )

    main, watch = partition_community_watch([candidate], config)

    assert main == [candidate]
    assert watch == []


def test_weak_stagnant_hn_topic_moves_to_community_watch(config: AppConfig) -> None:
    item = make_item(
        "Show HN: AI coding browser agent",
        provider="hacker_news",
        family="hacker_news",
        entity=None,
        authority="community",
        metrics={
            "points": 5,
            "comments": 0,
            "observed_growth": {
                "available": True,
                "observation_duration_hours": 4.0,
                "metrics": {
                    "points": {"initial": 5, "current": 5, "delta": 0},
                    "comments": {"initial": 0, "current": 0, "delta": 0},
                },
            },
        },
    )
    candidate = Candidate("stagnant", item.title, None, item.published_at, [item], ["hacker_news"])
    candidate.discovery_priority = 67.0

    main, watch = partition_community_watch([candidate], config)

    assert main == []
    assert watch == [candidate]
    assert candidate.presentation_gate["gate"] == "stagnant_community_interest"
    assert candidate.discovery_priority == 67.0


def test_cold_start_or_observed_growth_keeps_weak_hn_topic_in_main(config: AppConfig) -> None:
    cold = make_item(
        "Show HN: AI coding browser agent",
        provider="hacker_news",
        family="hacker_news",
        entity=None,
        authority="community",
        metrics={"points": 5, "comments": 0, "observed_growth": {"available": False}},
    )
    growing = replace(cold, external_id="growing")
    growing.metrics = {
        "points": 6,
        "comments": 0,
        "observed_growth": {
            "available": True,
            "observation_duration_hours": 4.0,
            "metrics": {
                "points": {"initial": 5, "current": 6, "delta": 1},
                "comments": {"initial": 0, "current": 0, "delta": 0},
            },
        },
    }
    candidates = [
        Candidate("cold", cold.title, None, cold.published_at, [cold], ["hacker_news"]),
        Candidate("growing", growing.title, None, growing.published_at, [growing], ["hacker_news"]),
    ]

    main, watch = partition_community_watch(candidates, config)

    assert main == candidates
    assert watch == []


def test_moderate_hn_interest_survives_stagnation_gate(config: AppConfig) -> None:
    points = config.ranking.interest.moderate_hn_points
    item = make_item(
        "Show HN: AI coding browser agent",
        provider="hacker_news",
        family="hacker_news",
        entity=None,
        authority="community",
        metrics={
            "points": points,
            "comments": 0,
            "observed_growth": {
                "available": True,
                "observation_duration_hours": 4.0,
                "metrics": {
                    "points": {"initial": points, "current": points, "delta": 0},
                    "comments": {"initial": 0, "current": 0, "delta": 0},
                },
            },
        },
    )
    candidate = Candidate("moderate", item.title, None, item.published_at, [item], ["hacker_news"])

    main, watch = partition_community_watch([candidate], config)

    assert main == [candidate]
    assert watch == []


def test_main_list_floor_requires_freshness_and_promotion_evidence(config: AppConfig) -> None:
    fresh_moderate_item = make_item(
        "Show HN: AI coding workflow",
        provider="hacker_news",
        family="hacker_news",
        authority="community",
    )
    fresh_moderate = Candidate(
        "fresh-moderate",
        fresh_moderate_item.title,
        None,
        fresh_moderate_item.published_at,
        [fresh_moderate_item],
        ["hacker_news"],
        freshness=70,
        interest_band="moderate",
        discovery_priority=65,
    )
    fresh_weak_item = make_item(
        "org/tiny-agent: AI coding tool",
        provider="github_explore",
        family="github",
        authority="community",
    )
    fresh_weak = Candidate(
        "fresh-weak",
        fresh_weak_item.title,
        None,
        fresh_weak_item.published_at,
        [fresh_weak_item],
        ["github"],
        freshness=70,
        interest_band="early/limited",
        discovery_priority=55,
    )
    old_strong_item = make_item(
        "Old but popular AI model",
        provider="huggingface",
        family="huggingface",
        authority="community",
    )
    old_strong = Candidate(
        "old-strong",
        old_strong_item.title,
        None,
        old_strong_item.published_at,
        [old_strong_item],
        ["huggingface"],
        freshness=30,
        interest_band="strong",
        discovery_priority=50,
    )

    promoted, watch = partition_main_list_floor(
        [fresh_moderate, fresh_weak, old_strong],
        config,
    )

    assert promoted == [fresh_moderate]
    assert watch == [fresh_weak, old_strong]
    assert fresh_weak.presentation_gate["gate"] == "main_list_floor"
    assert "no moderate/strong interest" in fresh_weak.presentation_gate["reason"]
    assert "below configured floor" in old_strong.presentation_gate["reason"]


def test_fresh_authoritative_event_passes_main_list_floor_without_interest(config: AppConfig) -> None:
    item = make_item("GitHub Copilot adds agent workflows", authority="official")
    candidate = Candidate(
        "official",
        item.title,
        item.entity,
        item.published_at,
        [item],
        ["official"],
        freshness=65,
        interest_band="early/limited",
        discovery_priority=60,
    )

    promoted, watch = partition_main_list_floor([candidate], config)

    assert promoted == [candidate]
    assert watch == []
    assert "authoritative event" in candidate.presentation_gate["reason"]


def test_main_list_floor_returns_fewer_results_without_changing_priority(config: AppConfig) -> None:
    values: list[Candidate] = []
    for index, interest_band in enumerate(["strong", "moderate", "early/limited"]):
        item = make_item(
            f"Community AI developer tool {index}",
            provider="github_explore",
            family="github",
            authority="community",
        )
        value = Candidate(
            str(index),
            item.title,
            None,
            item.published_at,
            [item],
            ["github"],
            freshness=60,
            interest_band=interest_band,
            discovery_priority=70 - index,
        )
        values.append(value)
    before = [value.discovery_priority for value in values]

    promoted, watch = partition_main_list_floor(values, config)

    assert len(promoted) == 2
    assert len(watch) == 1
    assert [value.discovery_priority for value in values] == before
