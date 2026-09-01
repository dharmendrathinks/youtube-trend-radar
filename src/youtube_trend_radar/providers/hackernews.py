from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.http import CachedHttpClient
from youtube_trend_radar.models import ProviderResult, SourceItem
from youtube_trend_radar.providers.common import combined_status, oldest_stale_at
from youtube_trend_radar.utils import clean_text, compact_error, normalize_url, parse_datetime


API = "https://hacker-news.firebaseio.com/v0"


def collect(config: AppConfig, client: CachedHttpClient, now: datetime) -> ProviderResult:
    failures: list[str] = []
    cache_states: list[tuple[str, datetime]] = []
    ids: set[int] = set()
    feeds = [str(value) for value in config.hacker_news.get("feeds", ["newstories", "showstories", "topstories"])]
    per_feed = int(config.hacker_news.get("ids_per_feed", 60))

    for feed in feeds:
        try:
            payload = client.get(f"{API}/{feed}.json")
            cache_states.append((payload.cache_state, payload.fetched_at))
            ids.update(int(value) for value in payload.json()[:per_feed])
        except Exception as exc:
            failures.append(f"{feed}: {compact_error(exc)}")

    stories: list[tuple[dict[str, Any], str, datetime]] = []

    def fetch(story_id: int) -> tuple[dict[str, Any], str, datetime]:
        payload = client.get(f"{API}/item/{story_id}.json")
        return payload.json(), payload.cache_state, payload.fetched_at

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch, story_id): story_id for story_id in ids}
        for future in as_completed(futures):
            try:
                story, state, fetched_at = future.result()
                if story:
                    stories.append((story, state, fetched_at))
            except Exception as exc:
                failures.append(f"item {futures[future]}: {compact_error(exc)}")

    cutoff = now - timedelta(days=config.lookback_days)
    items: list[SourceItem] = []
    for story, state, fetched_at in stories:
        cache_states.append((state, fetched_at))
        published = parse_datetime(datetime.fromtimestamp(int(story.get("time", 0)), tz=now.tzinfo))
        if not published or published < cutoff or story.get("deleted") or story.get("dead"):
            continue
        story_id = str(story["id"])
        discussion_url = f"https://news.ycombinator.com/item?id={story_id}"
        target_url = normalize_url(str(story.get("url") or discussion_url))
        items.append(
            SourceItem(
                provider="hacker_news",
                external_id=story_id,
                source_family="hacker_news",
                item_type="hacker_news_story",
                title=clean_text(story.get("title"), limit=300),
                summary=clean_text(story.get("text")),
                canonical_url=target_url,
                published_at=published,
                updated_at=None,
                observed_at=now,
                metrics={
                    "points": int(story.get("score", 0)),
                    "comments": int(story.get("descendants", 0)),
                    "author": str(story.get("by", "")),
                },
                related_links=[discussion_url] if target_url != normalize_url(discussion_url) else [],
            )
        )

    states = [state for state, _ in cache_states]
    return ProviderResult(
        provider="hacker_news",
        status=combined_status(item_count=len(items), failures=len(failures), cache_states=states),
        items=items,
        fetched_at=now,
        stale_as_of=oldest_stale_at(cache_states),
        error="; ".join(failures[:10]) or None,
        request_count=client.request_count,
    )

