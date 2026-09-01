from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import feedparser

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.http import CachedHttpClient
from youtube_trend_radar.models import ProviderResult, SourceItem
from youtube_trend_radar.providers.common import combined_status, oldest_stale_at
from youtube_trend_radar.utils import clean_text, compact_error, normalize_url, parse_datetime


def _entry_time(entry: Any, prefix: str) -> datetime | None:
    parsed = entry.get(f"{prefix}_parsed")
    return parse_datetime(parsed) or parse_datetime(entry.get(prefix))


def collect(config: AppConfig, client: CachedHttpClient, now: datetime) -> ProviderResult:
    items: list[SourceItem] = []
    failures: list[str] = []
    cache_states: list[tuple[str, datetime]] = []
    cutoff = now - timedelta(days=config.lookback_days)

    for feed in config.official_feeds:
        try:
            payload = client.get(feed.url)
            cache_states.append((payload.cache_state, payload.fetched_at))
            parsed = feedparser.parse(payload.body)
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"invalid feed: {clean_text(parsed.bozo_exception)}")
            for entry in parsed.entries:
                published = _entry_time(entry, "published") or _entry_time(entry, "updated")
                if published and published < cutoff:
                    continue
                link = normalize_url(str(entry.get("link") or ""))
                if not link:
                    continue
                title = clean_text(entry.get("title"), limit=300)
                if not title:
                    continue
                external_id = str(entry.get("id") or link)
                items.append(
                    SourceItem(
                        provider="official",
                        external_id=f"{feed.name}:{external_id}",
                        source_family="official",
                        item_type="official_announcement",
                        title=title,
                        summary=clean_text(entry.get("summary") or entry.get("description")),
                        canonical_url=link,
                        published_at=published,
                        updated_at=_entry_time(entry, "updated"),
                        observed_at=now,
                        entity=feed.entity,
                        authority="official",
                        metrics={"feed_name": feed.name},
                        related_links=[feed.url],
                    )
                )
        except Exception as exc:
            failures.append(f"{feed.name}: {compact_error(exc)}")

    states = [state for state, _ in cache_states]
    return ProviderResult(
        provider="official",
        status=combined_status(item_count=len(items), failures=len(failures), cache_states=states),
        items=items,
        fetched_at=now,
        stale_as_of=oldest_stale_at(cache_states),
        error="; ".join(failures) or None,
        request_count=client.request_count,
    )

