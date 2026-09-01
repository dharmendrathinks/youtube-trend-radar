from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import respx

from youtube_trend_radar.config import AppConfig, OfficialFeedConfig
from youtube_trend_radar.db import Database
from youtube_trend_radar.http import CachedHttpClient
from youtube_trend_radar.providers import github, hackernews, huggingface, official


NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


@respx.mock
def test_official_feed_provider(config: AppConfig) -> None:
    config = replace(config, official_feeds=[OfficialFeedConfig("Test", "https://example.test/feed.xml", "OpenAI")])
    database = Database(config.database_path)
    database.initialize()
    respx.get("https://example.test/feed.xml").mock(
        return_value=httpx.Response(
            200,
            text="""<rss version="2.0"><channel><title>Test</title><item><guid>x</guid><title>Codex SDK released</title><link>https://example.test/codex</link><pubDate>Tue, 01 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>""",
        )
    )
    result = official.collect(config, CachedHttpClient(database, config.http), NOW)
    assert result.status == "ok"
    assert result.items[0].authority == "official"


@respx.mock
def test_github_watched_and_exploration(config: AppConfig) -> None:
    config.github["watched_repositories"] = ["openai/codex"]
    config.github["exploration_queries"] = ["created:>{since} ai agent"]
    database = Database(config.database_path)
    database.initialize()
    repo = {
        "full_name": "openai/codex",
        "description": "AI coding agent",
        "html_url": "https://github.com/openai/codex",
        "created_at": "2026-08-30T00:00:00Z",
        "updated_at": "2026-09-01T10:00:00Z",
        "pushed_at": "2026-09-01T10:00:00Z",
        "stargazers_count": 100,
        "forks_count": 10,
        "open_issues_count": 2,
        "topics": ["coding-agent"],
    }
    respx.get("https://api.github.com/repos/openai/codex").mock(return_value=httpx.Response(200, json=repo))
    respx.get("https://api.github.com/repos/openai/codex/releases").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "tag_name": "v1.2.3", "name": "Codex v1.2.3", "body": "New agent workflow", "html_url": "https://github.com/openai/codex/releases/tag/v1.2.3", "published_at": "2026-09-01T09:00:00Z"}])
    )
    respx.get("https://api.github.com/search/repositories").mock(return_value=httpx.Response(200, json={"items": [repo]}))
    watched = github.collect_watched(config, github.build_client(config, database), NOW)
    explored = github.collect_exploratory(config, github.build_client(config, database), NOW)
    assert {item.item_type for item in watched.items} == {"github_repository_snapshot", "github_release"}
    assert explored.items[0].metrics["stars"] == 100


@respx.mock
def test_hacker_news_provider(config: AppConfig) -> None:
    config.hacker_news["feeds"] = ["newstories"]
    config.hacker_news["ids_per_feed"] = 1
    database = Database(config.database_path)
    database.initialize()
    respx.get("https://hacker-news.firebaseio.com/v0/newstories.json").mock(return_value=httpx.Response(200, json=[123]))
    respx.get("https://hacker-news.firebaseio.com/v0/item/123.json").mock(
        return_value=httpx.Response(200, json={"id": 123, "title": "Show HN: AI coding agent", "url": "https://example.test/agent", "time": int(NOW.timestamp()), "score": 42, "descendants": 12, "by": "dev"})
    )
    result = hackernews.collect(config, CachedHttpClient(database, config.http), NOW)
    assert result.status == "ok"
    assert result.items[0].metrics["comments"] == 12
    assert "news.ycombinator.com" in result.items[0].related_links[0]


def test_huggingface_provider(config: AppConfig, monkeypatch) -> None:
    class Info:
        id = "org/code-model"
        tags = ["text-generation", "code"]
        likes = 120
        downloads = 1000
        trending_score = 99
        created_at = NOW
        last_modified = NOW

    class FakeApi:
        def __init__(self, token=None):
            self.token = token

        def list_models(self, **kwargs):
            return [Info()]

        def list_spaces(self, **kwargs):
            return [Info()]

    monkeypatch.setattr(huggingface, "HfApi", FakeApi)
    result = huggingface.collect(config, NOW)
    assert result.status == "ok"
    assert {item.item_type for item in result.items} == {"huggingface_model", "huggingface_space"}
    assert result.items[0].metrics["trending_rank"] == 1

