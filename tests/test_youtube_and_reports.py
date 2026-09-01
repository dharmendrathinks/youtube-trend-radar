from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import respx

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.db import Database
from youtube_trend_radar.http import CachedHttpClient
from youtube_trend_radar.models import Candidate, ProviderResult, SourceItem
from youtube_trend_radar.providers.youtube import build_queries, build_viewer_intent, validate
from youtube_trend_radar.reports import build_report, render_markdown
from youtube_trend_radar.topics import attach_video_topics


NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def candidate() -> Candidate:
    item = SourceItem(
        provider="official",
        external_id="codex-bg",
        source_family="official",
        item_type="official_announcement",
        title="Codex Background Agents released",
        summary="Run coding agents in the background",
        canonical_url="https://openai.com/codex/background-agents",
        published_at=NOW - timedelta(hours=2),
        updated_at=None,
        observed_at=NOW,
        entity="OpenAI",
        authority="official",
    )
    return Candidate(
        fingerprint="abc",
        title=item.title,
        entity="OpenAI",
        effective_event_time=item.published_at,
        items=[item],
        source_families=["official"],
        freshness=97.0,
        evidence_level="authoritative primary source",
        evidence_value=90,
        interest_band="early/limited",
        interest_value=25,
        discovery_priority=84.45,
    )


def release_candidate(
    *,
    repository: str = "openai/codex",
    tag: str = "rust-v1.2.3",
    summary: str = "",
    title: str = "openai/codex 1.2.3",
) -> Candidate:
    item = SourceItem(
        provider="github_watched",
        external_id=f"{repository}:{tag}",
        source_family="github",
        item_type="github_release",
        title=title,
        summary=summary,
        canonical_url=f"https://github.com/{repository}/releases/tag/{tag}",
        published_at=NOW - timedelta(hours=2),
        updated_at=None,
        observed_at=NOW,
        entity="OpenAI" if repository == "openai/codex" else "Anthropic",
        authority="official",
        metrics={"repo_full_name": repository, "release_tag": tag},
    )
    return Candidate(
        fingerprint=f"release:{repository}:{tag}",
        title=item.title,
        entity=item.entity,
        effective_event_time=item.published_at,
        items=[item],
        source_families=["github"],
        freshness=97.0,
        evidence_level="authoritative primary source",
        evidence_value=90,
        interest_band="early/limited",
        interest_value=25,
        discovery_priority=84.45,
    )


def test_queries_are_specific() -> None:
    queries = build_queries(candidate())
    assert queries[0].startswith("Codex")
    assert "Background" in queries[0]
    assert queries != ["Codex"]


def test_version_only_release_is_explicitly_low_specificity() -> None:
    intent = build_viewer_intent(release_candidate())

    assert intent["specificity"] == "low"
    assert intent["queries"] == ["Codex 1.2.3 release"]
    assert "no meaningful standalone feature" in intent["basis"]


def test_release_notes_create_feature_specific_queries() -> None:
    intent = build_viewer_intent(
        release_candidate(
            summary=(
                "## New Features - Vim mode supports searches within drafts and highlighted matches. (#100) "
                "- MCP tools support an output_token_limit setting across session resumes. (#101)"
            )
        )
    )

    assert intent["specificity"] == "high"
    assert intent["queries"] == [
        "Codex Vim mode search",
        "Codex MCP tools output token limit",
    ]
    assert all("1.2.3" not in query for query in intent["queries"])
    assert all(query != "Codex" for query in intent["queries"])


def test_verbose_rate_limit_angle_becomes_short_natural_query() -> None:
    intent = build_viewer_intent(
        release_candidate(
            summary=(
                "## New Features - Rate-limit banners offer actions for checking usage, "
                "managing credits, resetting limits, and managing plans. (#200)"
            )
        )
    )

    assert intent["queries"] == ["Codex Rate limit credits"]


def test_compact_query_keeps_product_and_grounded_technical_terms() -> None:
    evidence = "Vim mode supports searches within drafts, highlighted matches, and repeat navigation."
    intent = build_viewer_intent(release_candidate(summary=f"## New Features - {evidence}"))
    query = intent["queries"][0]

    assert query == "Codex Vim mode search"
    assert all(term.lower() in f"Codex {evidence}".lower() for term in query.split())


def test_compact_query_removes_release_note_filler() -> None:
    intent = build_viewer_intent(
        release_candidate(
            summary=(
                "## New Features - Rate-limit banners offer actions for checking usage, "
                "managing credits, including highlighted controls."
            )
        )
    )
    query = intent["queries"][0].lower()

    for filler in ("banners", "offers", "actions", "checking", "usage", "managing", "including", "highlighted"):
        assert filler not in query


def test_version_is_kept_when_feature_intent_is_too_ambiguous() -> None:
    intent = build_viewer_intent(release_candidate(summary="## New Features - Adds API."))

    assert intent["queries"] == ["Codex 1.2.3 API"]


def test_query_count_respects_requested_limit() -> None:
    value = release_candidate(
        summary=(
            "## New Features - Vim mode supports search navigation. "
            "- MCP tools support output token limits. - Adds background agents."
        )
    )

    assert len(build_viewer_intent(value, limit=1)["queries"]) == 1
    assert len(build_viewer_intent(value, limit=2)["queries"]) == 2


def test_query_generation_does_not_change_priority_or_candidate_order() -> None:
    first = release_candidate(summary="## New Features - Vim mode supports search navigation.")
    second = candidate()
    values = [first, second]
    before = [(value.fingerprint, value.discovery_priority) for value in values]

    for value in values:
        build_viewer_intent(value)

    assert [(value.fingerprint, value.discovery_priority) for value in values] == before


def test_repository_identifier_does_not_leak_into_release_queries() -> None:
    intent = build_viewer_intent(
        release_candidate(
            repository="anthropics/claude-code",
            tag="v2.1.252",
            title="anthropics/claude-code v2.1.252",
            summary='## What changed - Fixed Bash commands failing with "task output swap refused" on some Macs',
        )
    )

    assert intent["product"] == "Claude Code"
    assert all("anthropics/claude-code" not in query.lower() for query in intent["queries"])
    assert all("/" not in query for query in intent["queries"])


def test_release_intent_does_not_add_tutorial_modifier() -> None:
    intent = build_viewer_intent(
        release_candidate(summary="## New Features - Background agents can run coding tasks asynchronously")
    )

    assert all("tutorial" not in query.lower() for query in intent["queries"])
    assert all("explained" not in query.lower() for query in intent["queries"])


def test_repository_project_is_humanized_without_becoming_product_only() -> None:
    item = SourceItem(
        provider="github_exploratory",
        external_id="deepseek-ai/DeepSeek-V4-Flash-Vision-Exp",
        source_family="github",
        item_type="github_exploratory_repository",
        title="deepseek-ai/DeepSeek-V4-Flash-Vision-Exp",
        summary="Experimental vision model for local inference",
        canonical_url="https://github.com/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp",
        published_at=NOW - timedelta(hours=3),
        updated_at=None,
        observed_at=NOW,
        entity="DeepSeek",
        authority="community",
    )
    value = Candidate(
        fingerprint="project",
        title=item.title,
        entity="DeepSeek",
        effective_event_time=item.published_at,
        items=[item],
        source_families=["github"],
        freshness=95,
        evidence_level="community",
        evidence_value=25,
        interest_band="early/limited",
        interest_value=25,
        discovery_priority=70,
    )

    queries = build_queries(value)
    assert all("deepseek-ai/" not in query.lower() for query in queries)
    assert all(query != "DeepSeek" for query in queries)
    assert "V4 Flash Vision Exp" in queries[0]


def test_missing_key_degrades_to_manual_links(config: AppConfig, monkeypatch) -> None:
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    database = Database(config.database_path)
    database.initialize()
    value = candidate()
    result = validate([value], config, CachedHttpClient(database, config.http), NOW)
    assert result.status == "disabled"
    assert value.youtube["included_in_discovery_priority"] is False
    assert value.youtube["manual_search_urls"]
    assert value.youtube["viewer_intent"]["basis"]
    assert value.youtube["local_relevance_annotations"]["status"] == "disabled"


@respx.mock
def test_youtube_search_and_hydration(config: AppConfig, monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    database = Database(config.database_path)
    database.initialize()
    respx.get("https://www.googleapis.com/youtube/v3/search").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": {"videoId": "vid2"}}, {"id": {"videoId": "vid1"}}]},
        )
    )
    respx.get("https://www.googleapis.com/youtube/v3/videos").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "vid1",
                        "snippet": {"title": "Codex explained", "channelTitle": "Dev", "publishedAt": "2026-09-01T10:00:00Z"},
                        "contentDetails": {"duration": "PT8M"},
                        "statistics": {"viewCount": "1234"},
                    },
                    {
                        "id": "vid2",
                        "snippet": {"title": "Unrelated result", "channelTitle": "Other", "publishedAt": "2026-09-01T11:00:00Z"},
                        "contentDetails": {"duration": "PT4M"},
                        "statistics": {"viewCount": "99"},
                    }
                ]
            },
        )
    )
    value = candidate()
    result = validate([value], config, CachedHttpClient(database, config.http, secrets=["test-key"]), NOW)
    assert result.status == "ok"
    assert [video["video_id"] for video in value.youtube["videos"]] == ["vid2", "vid1"]
    assert value.youtube["videos"][1]["views"] == 1234
    assert all("local_relevance" not in video for video in value.youtube["videos"])
    assert value.youtube["local_relevance_annotations"]["status"] == "disabled"
    assert value.youtube["included_in_discovery_priority"] is False
    search_request = next(call.request for call in respx.calls if call.request.url.path.endswith("/search"))
    params = search_request.url.params
    assert params["type"] == "video"
    assert params["order"] == "relevance"
    assert params["relevanceLanguage"] == "en"
    assert params["publishedAfter"] == "2026-08-02T00:00:00Z"
    assert params["q"] == value.youtube["queries"][0]
    assert value.youtube["api_queries"] == value.youtube["queries"]


@respx.mock
def test_opt_in_annotations_are_separate_and_do_not_filter_or_reorder(config: AppConfig, monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    config.youtube["enable_local_relevance_annotations"] = True
    database = Database(config.database_path)
    database.initialize()
    respx.get("https://www.googleapis.com/youtube/v3/search").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": {"videoId": "unrelated"}}, {"id": {"videoId": "strong"}}]},
        )
    )
    respx.get("https://www.googleapis.com/youtube/v3/videos").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "strong",
                        "snippet": {
                            "title": "Codex background agents workflow",
                            "channelTitle": "Developer News",
                            "publishedAt": "2026-09-01T10:00:00Z",
                        },
                        "contentDetails": {"duration": "PT8M"},
                        "statistics": {},
                    },
                    {
                        "id": "unrelated",
                        "snippet": {
                            "title": "Anime flight game",
                            "channelTitle": "Arcade",
                            "publishedAt": "2026-09-01T11:00:00Z",
                        },
                        "contentDetails": {"duration": "PT4M"},
                        "statistics": {},
                    },
                ]
            },
        )
    )

    value = candidate()
    before = value.discovery_priority
    validate([value], config, CachedHttpClient(database, config.http, secrets=["test-key"]), NOW)

    assert [video["video_id"] for video in value.youtube["videos"]] == ["unrelated", "strong"]
    assert [video["title"] for video in value.youtube["videos"]] == [
        "Anime flight game",
        "Codex background agents workflow",
    ]
    assert [video["local_relevance"]["label"] for video in value.youtube["videos"]] == [
        "unrelated",
        "strong intent match",
    ]
    assert value.youtube["local_relevance_annotations"]["youtube_supplied"] is False
    assert value.youtube["local_relevance_annotations"]["preserves_youtube_order"] is True
    assert value.discovery_priority == before


def test_report_is_deterministic_and_explicit(config: AppConfig) -> None:
    value = candidate()
    value.youtube = {
        "status": "disabled",
        "queries": ["Codex background agents"],
        "api_queries": ["Codex background agents"],
        "manual_search_urls": ["https://www.youtube.com/results?search_query=Codex+background+agents"],
        "videos": [
            {
                "title": "Codex background agents workflow",
                "url": "https://www.youtube.com/watch?v=test",
                "channel": "Developer News",
                "published_at": "2026-09-01T10:00:00Z",
                "views": 10,
                "local_relevance": {"label": "strong intent match"},
            }
        ],
        "reason": "no key",
        "local_relevance_annotations": {
            "status": "enabled",
            "reason": "operator enabled policy-gated local content categorization",
            "policy_url": "https://developers.google.com/youtube/terms/derived-metrics-policy",
        },
        "viewer_intent": {
            "type": "event",
            "specificity": "high",
            "basis": "human-readable event title and product identity",
        },
    }
    providers = [ProviderResult("official", "ok", value.items, NOW), ProviderResult("youtube", "disabled", [], NOW, error="no key")]
    report = build_report(
        scan_id="scan1",
        started_at=NOW,
        completed_at=NOW,
        status="complete",
        config=config,
        provider_results=providers,
        candidates=[value],
    )
    assert render_markdown(report) == render_markdown(report)
    markdown = render_markdown(report)
    assert "not included in Discovery Priority" in markdown
    assert "specificity=high" in markdown
    assert "Exact YouTube search: [Codex background agents]" in markdown
    assert "judge intent relevance manually" in markdown
    assert "Client-generated relevance annotations: enabled" in markdown
    assert "Client annotation: strong intent match" in markdown
    assert "YouTube result order and content are preserved" in markdown
    assert report["effective_interest_thresholds"]["strong_hn_points"] == 100
    assert report["effective_eligibility_thresholds"]["community_hn_min_points"] == 5


def test_report_uses_angle_title_and_preserves_parent_release(config: AppConfig) -> None:
    value = release_candidate(
        summary=(
            "## New Features - Vim mode supports search navigation within command drafts. (#50) "
            "- MCP tools support output token limits. (#51)"
        )
    )
    attach_video_topics([value])
    value.youtube = {
        "status": "disabled",
        "queries": [value.video_topic["primary_angle"]["query"]],
        "manual_search_urls": [],
        "videos": [],
        "reason": "no key",
        "viewer_intent": build_viewer_intent(value),
    }
    providers = [
        ProviderResult("github_watched", "ok", value.items, NOW),
        ProviderResult("youtube", "disabled", [], NOW, error="no key"),
    ]

    report = build_report(
        scan_id="release-scan",
        started_at=NOW,
        completed_at=NOW,
        status="complete",
        config=config,
        provider_results=providers,
        candidates=[value],
    )
    recommendation = report["recommendations"][0]
    markdown = render_markdown(report)

    assert recommendation["title"] == "openai/codex 1.2.3"
    assert recommendation["display_title"].startswith("Codex:")
    assert recommendation["video_topic"]["parent_event_id"] == "openai/codex:rust-v1.2.3"
    assert "### 1. Codex:" in markdown
    assert "**Release:** [openai/codex 1.2.3]" in markdown
    assert "**Potential video angles:**" in markdown


def test_report_preserves_low_topicability_release_in_release_watch(config: AppConfig) -> None:
    watched = release_candidate(summary="## Maintenance - Updated packages and bumped dependencies.")
    attach_video_topics([watched])
    priority = watched.discovery_priority
    providers = [ProviderResult("github_watched", "ok", watched.items, NOW)]

    report = build_report(
        scan_id="watch-scan",
        started_at=NOW,
        completed_at=NOW,
        status="complete",
        config=config,
        provider_results=providers,
        candidates=[],
        release_watch=[watched],
    )
    markdown = render_markdown(report)

    assert report["recommendations"] == []
    assert report["release_watch"][0]["discovery_priority"] == priority
    assert report["release_watch"][0]["topicability"]["status"] == "release_watch"
    assert "## Release Watch" in markdown
    assert "fresh release has no defensible standalone video angle" in markdown


def test_report_preserves_gated_candidate_in_community_watch(config: AppConfig) -> None:
    value = candidate()
    value.presentation_gate = {
        "status": "community_watch",
        "gate": "language",
        "reason": "predominantly non-Latin-script",
        "measurements": {"letter_count": 100, "latin_letter_ratio": 0.2},
        "thresholds": {"minimum_latin_letter_ratio": 0.6},
    }
    priority = value.discovery_priority
    report = build_report(
        scan_id="community-watch-scan",
        started_at=NOW,
        completed_at=NOW,
        status="complete",
        config=config,
        provider_results=[ProviderResult("github_explore", "ok", value.items, NOW)],
        candidates=[],
        community_watch=[value],
    )
    markdown = render_markdown(report)

    assert report["recommendations"] == []
    assert report["community_watch"][0]["discovery_priority"] == priority
    assert report["community_watch"][0]["presentation_gate"]["gate"] == "language"
    assert "## Community Watch" in markdown
    assert "predominantly non-Latin-script" in markdown
