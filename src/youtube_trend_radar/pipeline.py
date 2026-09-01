from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4
import logging
import os
import sys

from youtube_trend_radar.config import ConfigError, load_config
from youtube_trend_radar.db import Database
from youtube_trend_radar.http import CachedHttpClient
from youtube_trend_radar.models import ProviderResult
from youtube_trend_radar.providers import github, hackernews, huggingface, official, youtube
from youtube_trend_radar.ranking import (
    SCORING_VERSION,
    attach_repository_support,
    eligible_items,
    filter_eligible_candidates,
    partition_community_watch,
    partition_main_list_floor,
    rank_candidates,
)
from youtube_trend_radar.reports import build_report, write_reports
from youtube_trend_radar.resolution import cluster_items
from youtube_trend_radar.topics import attach_video_topics, partition_topicable_candidates
from youtube_trend_radar.utils import compact_error


LOGGER = logging.getLogger(__name__)
DISCOVERY_PROVIDERS = ["official", "github_watched", "github_explore", "hacker_news", "huggingface"]


def _failed(name: str, now: datetime, exc: BaseException) -> ProviderResult:
    return ProviderResult(name, "failed", [], now, error=compact_error(exc))


def run_scan(config_path: Path, *, top: int | None = None, no_youtube: bool = False) -> int:
    started = datetime.now(UTC)
    scan_id = uuid4().hex[:10]
    try:
        config = load_config(config_path)
        if top is not None and top <= 0:
            raise ConfigError("--top must be positive")
        result_count = top or config.top_results
        database = Database(config.database_path)
        database.initialize()

        tasks: dict[str, Callable[[], ProviderResult]] = {
            "official": lambda: official.collect(config, CachedHttpClient(database, config.http), started),
            "github_watched": lambda: github.collect_watched(config, github.build_client(config, database), started),
            "github_explore": lambda: github.collect_exploratory(config, github.build_client(config, database), started),
            "hacker_news": lambda: hackernews.collect(config, CachedHttpClient(database, config.http), started),
            "huggingface": lambda: huggingface.collect(config, started),
        }
        collected: dict[str, ProviderResult] = {}
        with ThreadPoolExecutor(max_workers=min(config.max_workers, len(tasks))) as executor:
            futures = {executor.submit(task): name for name, task in tasks.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    collected[name] = future.result()
                except Exception as exc:
                    collected[name] = _failed(name, started, exc)
                result = collected[name]
                LOGGER.info("%s: %s (%d items)", name, result.status, len(result.items))

        provider_results = [collected[name] for name in DISCOVERY_PROVIDERS]
        usable = [result for result in provider_results if result.status in {"ok", "partial", "cached", "stale"}]
        if not usable:
            details = "; ".join(f"{result.provider}: {result.error}" for result in provider_results)
            raise RuntimeError(f"all discovery providers are unavailable: {details}")

        all_items = []
        for result in provider_results:
            for item in result.items:
                database.add_observed_growth(item)
            database.record_provider_result(result)
            all_items.extend(result.items)

        eligible = eligible_items(all_items, config, started)
        candidates = cluster_items(eligible, config)
        attach_repository_support(candidates, all_items)
        candidates = rank_candidates(filter_eligible_candidates(candidates, config), config, started)
        candidates, community_watch = partition_community_watch(candidates, config)
        attach_video_topics(candidates, config.topics)
        topicable, release_watch = partition_topicable_candidates(candidates, len(candidates))
        promoted, floor_watch = partition_main_list_floor(topicable, config)
        selected = promoted[:result_count]
        release_watch.extend(
            candidate
            for candidate in floor_watch
            if candidate.video_topic
            or any(item.authority == "official" or item.source_family == "official" for item in candidate.items)
        )
        community_watch.extend(
            candidate
            for candidate in floor_watch
            if not candidate.video_topic
            and not any(item.authority == "official" or item.source_family == "official" for item in candidate.items)
        )
        release_watch = sorted(
            release_watch,
            key=lambda candidate: (candidate.discovery_priority, candidate.effective_event_time, candidate.fingerprint),
            reverse=True,
        )[:result_count]
        community_watch = sorted(
            community_watch,
            key=lambda candidate: (candidate.discovery_priority, candidate.effective_event_time, candidate.fingerprint),
            reverse=True,
        )[:result_count]
        unavailable_discovery = [result.provider for result in provider_results if result.status in {"failed", "stale"}]
        for candidate in [*selected, *release_watch, *community_watch]:
            if unavailable_discovery:
                candidate.missing.append(f"Discovery evidence unavailable or stale: {', '.join(unavailable_discovery)}")
            if any(
                item.metrics.get("observed_growth") and not item.metrics["observed_growth"].get("available")
                for item in candidate.items
            ):
                candidate.missing.append("Observed growth unavailable on first observation; current aggregate shown")

        youtube_key = os.getenv("YOUTUBE_API_KEY")
        youtube_client = CachedHttpClient(database, config.http, secrets=[youtube_key] if youtube_key else [])
        youtube_result = youtube.validate(selected, config, youtube_client, started, disabled=no_youtube)
        provider_results.append(youtube_result)

        completed = datetime.now(UTC)
        discovery_partial = any(result.status in {"failed", "partial", "stale"} for result in provider_results[:-1])
        youtube_requested = bool(config.youtube.get("enabled", True)) and not no_youtube
        youtube_partial = youtube_requested and youtube_result.status not in {"ok", "cached"}
        partial = discovery_partial or youtube_partial
        status = "partial" if partial else "complete"
        report = build_report(
            scan_id=scan_id,
            started_at=started,
            completed_at=completed,
            status=status,
            config=config,
            provider_results=provider_results,
            candidates=selected,
            release_watch=release_watch,
            community_watch=community_watch,
        )
        markdown_path, json_path = write_reports(report, config.reports_path)
        database.record_scan(
            scan_id=scan_id,
            started_at=started,
            completed_at=completed,
            status=status,
            config_fingerprint=config.fingerprint,
            scoring_version=SCORING_VERSION,
            provider_statuses=[result.status_dict() for result in provider_results],
            report=report,
        )
        print(
            f"Scan {scan_id}: {status}; {len(selected)} recommendations; "
            f"{len(release_watch)} release watch; {len(community_watch)} community watch"
        )
        print(f"Markdown: {markdown_path}")
        print(f"JSON: {json_path}")
        return 0
    except (ConfigError, OSError, RuntimeError, ValueError) as exc:
        print(f"scan failed: {compact_error(exc)}", file=sys.stderr)
        return 1
