from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote_plus
import os
import re

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.http import CachedHttpClient
from youtube_trend_radar.models import Candidate, ProviderResult
from youtube_trend_radar.providers.common import combined_status, oldest_stale_at
from youtube_trend_radar.topics import (
    QUERY_STOPWORDS,
    WORD_RE,
    extract_release_topic,
    product_name,
    query_text,
    release_item,
    without_repository_syntax,
)
from youtube_trend_radar.utils import clean_text, compact_error


SEARCH_API = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_API = "https://www.googleapis.com/youtube/v3/videos"
DERIVED_METRICS_POLICY_URL = "https://developers.google.com/youtube/terms/derived-metrics-policy"
ANNOTATION_STOPWORDS = QUERY_STOPWORDS | {"release", "mode"}


def _annotation_context(intent: dict[str, Any], query: str) -> dict[str, Any]:
    product = str(intent.get("product") or "").lower()
    product_words = {word.lower() for word in WORD_RE.findall(product)}
    feature_anchors = []
    for word in WORD_RE.findall(query.lower().replace("-", " ")):
        if word in product_words or word in ANNOTATION_STOPWORDS or word.isdigit():
            continue
        if len(word) >= 3 and word not in feature_anchors:
            feature_anchors.append(word)
    return {"product": product, "feature_anchors": feature_anchors}


def _contains_anchor(text: str, anchor: str) -> bool:
    return bool(anchor and re.search(rf"(?<![a-z0-9]){re.escape(anchor)}(?![a-z0-9])", text))


def _local_relevance_annotation(video: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    haystack = f"{video['title']} {video['channel']}".lower().replace("-", " ")
    product = str(context["product"])
    product_match = _contains_anchor(haystack, product)
    matched_anchors = [anchor for anchor in context["feature_anchors"] if _contains_anchor(haystack, anchor)]
    if product_match and matched_anchors:
        label = "strong intent match"
        reason = "title/channel contains the product and a feature anchor"
    elif product_match or matched_anchors:
        label = "weak intent match"
        reason = "title/channel contains only part of the intent anchors"
    else:
        label = "unrelated"
        reason = "title/channel contains neither the product nor feature anchors"
    return {
        "label": label,
        "reason": reason,
        "matched_feature_anchors": matched_anchors,
        "analysis_source": "youtube-trend-radar deterministic title/channel annotation",
        "youtube_supplied": False,
    }


def _annotation_policy(config: AppConfig) -> dict[str, Any]:
    enabled = bool(config.youtube.get("enable_local_relevance_annotations", False))
    return {
        "status": "enabled" if enabled else "disabled",
        "reason": (
            "operator enabled policy-gated local content categorization"
            if enabled
            else "disabled by default; enabling requires acceptance of YouTube's derived-metrics amendment"
        ),
        "policy_url": DERIVED_METRICS_POLICY_URL,
        "preserves_youtube_order": True,
        "youtube_supplied": False,
    }


def build_viewer_intent(candidate: Candidate, limit: int = 2) -> dict[str, Any]:
    product = product_name(candidate)
    release = release_item(candidate)
    if release:
        topic = candidate.video_topic or extract_release_topic(candidate)
        angles = [topic["primary_angle"], *topic["alternative_angles"]]
        queries = list(dict.fromkeys(angle["query"] for angle in angles))[:limit]
        return {
            "type": "release",
            "product": product,
            "version": topic["release_version"],
            "specificity": topic["specificity"],
            "basis": topic["fallback_reason"] or "queries derived from extracted release video angles",
            "angle_ids": [angle["angle_id"] for angle in angles[:limit]],
            "queries": queries,
        }

    title = re.sub(r"^(?:Show|Launch) HN:\s*", "", candidate.title, flags=re.I)
    title = without_repository_syntax(title)
    title_words = [word for word in WORD_RE.findall(title) if word.lower() not in QUERY_STOPWORDS]
    distinctive = " ".join(title_words[:10])
    primary = query_text(product, distinctive)
    item_types = {item.item_type for item in candidate.items}
    is_project = any(value in {"github_exploratory_repository", "huggingface_space", "github_new_repository"} for value in item_types)
    queries = [primary]
    if is_project and limit > 1:
        queries.append(query_text(product, " ".join(title_words[:6]), "demo"))
    return {
        "type": "project" if is_project else "event",
        "product": product,
        "version": None,
        "specificity": "high" if len(title_words) >= 3 else "medium",
        "basis": "human-readable event title and product identity",
        "feature_phrases": [],
        "queries": list(dict.fromkeys(queries))[:limit],
    }


def build_queries(candidate: Candidate, limit: int = 2) -> list[str]:
    return list(build_viewer_intent(candidate, limit)["queries"])


def _video(details: dict[str, Any], query: str, api_query: str) -> dict[str, Any]:
    snippet = details.get("snippet", {})
    statistics = details.get("statistics", {})
    return {
        "video_id": details["id"],
        "title": clean_text(snippet.get("title"), limit=300),
        "channel": clean_text(snippet.get("channelTitle"), limit=200),
        "published_at": snippet.get("publishedAt"),
        "url": f"https://www.youtube.com/watch?v={details['id']}",
        "duration": details.get("contentDetails", {}).get("duration"),
        "views": int(statistics["viewCount"]) if statistics.get("viewCount", "").isdigit() else None,
        "likes": int(statistics["likeCount"]) if statistics.get("likeCount", "").isdigit() else None,
        "comments": int(statistics["commentCount"]) if statistics.get("commentCount", "").isdigit() else None,
        "matched_query": query,
        "youtube_api_query": api_query,
    }


def validate(
    candidates: list[Candidate],
    config: AppConfig,
    client: CachedHttpClient,
    now: datetime,
    *,
    disabled: bool = False,
) -> ProviderResult:
    key = os.getenv("YOUTUBE_API_KEY")
    target_count = min(len(candidates), int(config.youtube.get("candidates", 10)))
    targets = candidates[:target_count]
    annotations = _annotation_policy(config)
    if disabled or not bool(config.youtube.get("enabled", True)) or not key:
        reason = "disabled by command/configuration" if disabled or not bool(config.youtube.get("enabled", True)) else "YOUTUBE_API_KEY is not set"
        for candidate in targets:
            intent = build_viewer_intent(candidate, int(config.youtube.get("queries_per_candidate", 2)))
            queries = intent["queries"]
            api_queries = list(queries)
            candidate.youtube = {
                "status": "disabled",
                "reason": reason,
                "queries": queries,
                "api_queries": api_queries,
                "manual_search_urls": [f"https://www.youtube.com/results?search_query={quote_plus(query)}" for query in api_queries],
                "videos": [],
                "viewer_intent": intent,
                "local_relevance_annotations": annotations,
                "included_in_discovery_priority": False,
            }
        return ProviderResult("youtube", "disabled", [], now, error=reason)

    failures: list[str] = []
    cache_states: list[tuple[str, datetime]] = []
    search_budget = int(config.youtube.get("request_budget", 20))
    queries_per_candidate = int(config.youtube.get("queries_per_candidate", 2))
    results_per_query = min(50, int(config.youtube.get("results_per_query", 10)))
    lookback_days = int(config.youtube.get("lookback_days", 30))
    cache_ttl = timedelta(hours=int(config.youtube.get("cache_ttl_hours", 6)))
    published_after = (now - timedelta(days=lookback_days)).date().isoformat() + "T00:00:00Z"
    used_searches = 0

    for candidate in targets:
        intent = build_viewer_intent(candidate, queries_per_candidate)
        queries = intent["queries"]
        api_queries = list(queries)
        ordered_ids: list[tuple[str, str, str]] = []
        candidate_failures: list[str] = []
        for query, api_query in zip(queries, api_queries, strict=True):
            if used_searches >= search_budget:
                candidate_failures.append("per-scan YouTube search budget exhausted")
                break
            used_searches += 1
            try:
                payload = client.get(
                    SEARCH_API,
                    params={
                        "key": key,
                        "part": "snippet",
                        "type": "video",
                        "q": api_query,
                        "publishedAfter": published_after,
                        "relevanceLanguage": str(config.youtube.get("relevance_language", "en")),
                        "maxResults": results_per_query,
                        "order": "relevance",
                    },
                    ttl=cache_ttl,
                )
                cache_states.append((payload.cache_state, payload.fetched_at))
                known_ids = {value for value, _, _ in ordered_ids}
                for result in payload.json().get("items", []):
                    video_id = result.get("id", {}).get("videoId")
                    if video_id and video_id not in known_ids:
                        ordered_ids.append((video_id, query, api_query))
                        known_ids.add(video_id)
            except Exception as exc:
                candidate_failures.append(f"query {query!r}: {compact_error(exc, [key])}")

        videos: list[dict[str, Any]] = []
        if ordered_ids:
            try:
                payload = client.get(
                    VIDEOS_API,
                    params={
                        "key": key,
                        "part": "snippet,contentDetails,statistics",
                        "id": ",".join(video_id for video_id, _, _ in ordered_ids),
                    },
                    ttl=cache_ttl,
                )
                cache_states.append((payload.cache_state, payload.fetched_at))
                by_id = {value["id"]: value for value in payload.json().get("items", [])}
                videos = [
                    _video(by_id[video_id], query, api_query)
                    for video_id, query, api_query in ordered_ids
                    if video_id in by_id
                ]
                if annotations["status"] == "enabled":
                    contexts = {query: _annotation_context(intent, query) for query in queries}
                    for video in videos:
                        video["local_relevance"] = _local_relevance_annotation(
                            video,
                            contexts[video["matched_query"]],
                        )
            except Exception as exc:
                candidate_failures.append(f"video details: {compact_error(exc, [key])}")

        candidate.youtube = {
            "status": "partial" if candidate_failures else "ok",
            "queries": queries,
            "api_queries": api_queries,
            "manual_search_urls": [f"https://www.youtube.com/results?search_query={quote_plus(query)}" for query in api_queries],
            "videos": videos,
            "viewer_intent": intent,
            "local_relevance_annotations": annotations,
            "errors": candidate_failures,
            "included_in_discovery_priority": False,
        }
        failures.extend(f"{candidate.fingerprint}: {message}" for message in candidate_failures)

    states = [state for state, _ in cache_states]
    status = combined_status(
        item_count=sum(len(candidate.youtube.get("videos", [])) for candidate in targets),
        failures=len(failures),
        cache_states=states,
    )
    return ProviderResult(
        provider="youtube",
        status=status,
        items=[],
        fetched_at=now,
        stale_as_of=oldest_stale_at(cache_states),
        error="; ".join(failures[:10]) or None,
        request_count=client.request_count,
    )
