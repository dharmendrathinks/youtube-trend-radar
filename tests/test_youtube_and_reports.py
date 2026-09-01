from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import respx

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.db import Database
from youtube_trend_radar.http import CachedHttpClient
from youtube_trend_radar.models import Candidate, ProviderResult, SourceItem
from youtube_trend_radar.providers.youtube import build_queries, validate
from youtube_trend_radar.reports import build_report, render_markdown


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


def test_queries_are_specific() -> None:
    queries = build_queries(candidate())
    assert queries[0].startswith("OpenAI")
    assert "Background" in queries[0]
    assert queries[1].endswith("explained")


def test_missing_key_degrades_to_manual_links(config: AppConfig, monkeypatch) -> None:
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    database = Database(config.database_path)
    database.initialize()
    value = candidate()
    result = validate([value], config, CachedHttpClient(database, config.http), NOW)
    assert result.status == "disabled"
    assert value.youtube["included_in_discovery_priority"] is False
    assert value.youtube["manual_search_urls"]


@respx.mock
def test_youtube_search_and_hydration(config: AppConfig, monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    database = Database(config.database_path)
    database.initialize()
    respx.get("https://www.googleapis.com/youtube/v3/search").mock(
        return_value=httpx.Response(200, json={"items": [{"id": {"videoId": "vid1"}}]})
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
                    }
                ]
            },
        )
    )
    value = candidate()
    result = validate([value], config, CachedHttpClient(database, config.http, secrets=["test-key"]), NOW)
    assert result.status == "ok"
    assert value.youtube["videos"][0]["views"] == 1234
    assert value.youtube["included_in_discovery_priority"] is False


def test_report_is_deterministic_and_explicit(config: AppConfig) -> None:
    value = candidate()
    value.youtube = {"status": "disabled", "queries": [], "manual_search_urls": [], "videos": [], "reason": "no key"}
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
    assert "not included in Discovery Priority" in render_markdown(report)
    assert report["effective_interest_thresholds"]["strong_hn_points"] == 100

