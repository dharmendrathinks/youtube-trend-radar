from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import respx

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.db import Database
from youtube_trend_radar.http import CachedHttpClient
from youtube_trend_radar.models import ProviderResult, SourceItem


def repo_item(now: datetime, stars: int) -> SourceItem:
    return SourceItem(
        provider="github_watched",
        external_id="openai/codex",
        source_family="github",
        item_type="github_repository_snapshot",
        title="openai/codex",
        summary="AI coding agent",
        canonical_url="https://github.com/openai/codex",
        published_at=now,
        updated_at=now,
        observed_at=now,
        entity="OpenAI",
        metrics={"stars": stars},
    )


def test_repeated_observations_report_only_observed_delta(config: AppConfig) -> None:
    database = Database(config.database_path)
    database.initialize()
    first_time = datetime(2026, 9, 1, 0, tzinfo=UTC)
    first = repo_item(first_time, 100)
    database.add_observed_growth(first)
    assert first.metrics["observed_growth"]["available"] is False
    database.record_provider_result(ProviderResult("github_watched", "ok", [first], first_time))

    second = repo_item(first_time + timedelta(hours=6), 112)
    database.add_observed_growth(second)
    growth = second.metrics["observed_growth"]
    assert growth["available"] is True
    assert growth["metrics"]["stars"] == {"initial": 100, "current": 112, "delta": 12}
    assert growth["observation_duration_hours"] == 6


@respx.mock
def test_http_cache_and_stale_fallback_redact_secret(config: AppConfig) -> None:
    database = Database(config.database_path)
    database.initialize()
    http_config = replace(config.http, max_retries=0)
    client = CachedHttpClient(database, http_config, secrets=["secret-key"])
    route = respx.get("https://api.example.test/items?key=secret-key&q=codex").mock(
        return_value=httpx.Response(200, json={"items": [1]}, headers={"ETag": "abc"})
    )
    first = client.get(
        "https://api.example.test/items",
        params={"key": "secret-key", "q": "codex"},
        ttl=timedelta(seconds=-1),
    )
    assert first.cache_state == "live"
    route.side_effect = httpx.ConnectError("offline")
    second = client.get(
        "https://api.example.test/items",
        params={"key": "secret-key", "q": "codex"},
        ttl=timedelta(seconds=-1),
    )
    assert second.cache_state == "stale"
    with database.connect() as connection:
        stored = connection.execute("SELECT url FROM http_cache").fetchone()[0]
    assert "secret-key" not in stored
    assert "%3Credacted%3E" in stored or "%3Credacted%3E".lower() in stored.lower()

