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
from youtube_trend_radar.utils import clean_text, compact_error


SEARCH_API = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_API = "https://www.googleapis.com/youtube/v3/videos"
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._/-]*")
REPOSITORY_RE = re.compile(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b")
ISSUE_RE = re.compile(r"\s*\(#\d+(?:,\s*#\d+)*\)")
PRODUCT_PATTERNS = [
    ("claude code", "Claude Code"),
    ("gemini cli", "Gemini CLI"),
    ("github copilot", "GitHub Copilot"),
    ("model context protocol", "MCP"),
    ("modelcontextprotocol", "MCP"),
    ("openrouter", "OpenRouter"),
    ("hugging face", "Hugging Face"),
    ("huggingface", "Hugging Face"),
    ("deepseek", "DeepSeek"),
    ("ollama", "Ollama"),
    ("codex", "Codex"),
    ("cursor", "Cursor"),
]
REPOSITORY_PRODUCTS = {
    "openai/codex": "Codex",
    "anthropics/claude-code": "Claude Code",
    "google-gemini/gemini-cli": "Gemini CLI",
    "modelcontextprotocol/servers": "MCP Servers",
    "ollama/ollama": "Ollama",
    "huggingface/huggingface_hub": "Hugging Face Hub",
}
QUERY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "can",
    "for", "from", "has", "have", "in", "including", "into", "is", "it", "its",
    "new", "now", "of", "on", "or", "that", "the", "their", "through", "to",
    "was", "were", "when", "with", "within", "fixed", "fixes", "added", "adds",
    "support", "supports", "updated", "update", "changed", "changes",
}


def _release_item(candidate: Candidate):
    return next((item for item in candidate.items if item.item_type == "github_release"), None)


def _repository(candidate: Candidate) -> str | None:
    return next(
        (str(item.metrics["repo_full_name"]).lower() for item in candidate.items if item.metrics.get("repo_full_name")),
        None,
    )


def _humanize_slug(value: str) -> str:
    words = re.sub(r"[-_]+", " ", value).split()
    return " ".join(word.upper() if word.lower() in {"ai", "api", "cli", "mcp", "sdk"} else word.capitalize() for word in words)


def _product_name(candidate: Candidate) -> str:
    repository = _repository(candidate)
    if repository in REPOSITORY_PRODUCTS:
        return REPOSITORY_PRODUCTS[repository]
    title_lower = candidate.title.lower()
    for pattern, product in PRODUCT_PATTERNS:
        if pattern in title_lower:
            return product
    if repository:
        return _humanize_slug(repository.split("/", 1)[-1])
    title = re.sub(r"^(?:Show|Launch) HN:\s*", "", candidate.title, flags=re.I)
    return clean_text(re.split(r"\s+[–—:-]\s+", title, maxsplit=1)[0], limit=50) or candidate.entity or "AI developer tool"


def _release_version(candidate: Candidate) -> str | None:
    item = _release_item(candidate)
    if not item:
        return None
    tag = str(item.metrics.get("release_tag") or "").strip()
    tag = re.sub(r"^(?:rust-)?v(?=\d)", "", tag, flags=re.I)
    return tag or None


def _feature_phrase(line: str) -> str | None:
    text = re.split(r"\s+#{1,6}\s+", line, maxsplit=1)[0]
    text = ISSUE_RE.sub("", text.strip().lstrip("-* "))
    text = re.sub(r"[`*_#]", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\([^)]{35,}\)", "", text)
    tokens: list[str] = []
    seen: set[str] = set()
    for token in WORD_RE.findall(text):
        clean = token.strip("./").replace("_", " ")
        lower = clean.lower()
        if not clean or lower in QUERY_STOPWORDS or lower in seen or lower.isdigit():
            continue
        if token.startswith("#"):
            continue
        seen.add(lower)
        tokens.append(clean)
        if len(tokens) == 8:
            break
    return " ".join(tokens) if len(tokens) >= 3 else None


def _release_features(summary: str, limit: int = 2) -> list[str]:
    package_names = re.findall(r"@modelcontextprotocol/server-([a-z-]+)@", summary, flags=re.I)
    if package_names:
        names = [name.replace("-", " ") for name in package_names[:4]]
        return [f"{' '.join(names)} server updates"]
    phrases: list[str] = []
    # Source summaries are normalized to one line before storage. Split both
    # ordinary Markdown lists and their whitespace-compacted representation.
    bullets = re.split(r"(?:^|\s)[-*]\s+", summary)
    for bullet in bullets[1:]:
        phrase = _feature_phrase(bullet)
        if phrase and phrase.lower() not in {value.lower() for value in phrases}:
            phrases.append(phrase)
        if len(phrases) == limit:
            break
    return phrases


def _without_repository_syntax(text: str) -> str:
    def human_name(match: re.Match[str]) -> str:
        return _humanize_slug(match.group(0).split("/", 1)[1])

    return clean_text(REPOSITORY_RE.sub(human_name, text).replace("/", " "), limit=100)


def _query(product: str, *parts: str | None) -> str:
    text = " ".join([product, *(part for part in parts if part)])
    text = _without_repository_syntax(text)
    words: list[str] = []
    seen: set[str] = set()
    for word in text.split():
        word = word.strip("()[]{}:;,")
        lower = word.lower()
        if not word:
            continue
        if lower not in seen:
            seen.add(lower)
            words.append(word)
    return clean_text(" ".join(words), limit=100)


def build_viewer_intent(candidate: Candidate, limit: int = 2) -> dict[str, Any]:
    product = _product_name(candidate)
    release = _release_item(candidate)
    if release:
        version = _release_version(candidate)
        features = _release_features(release.summary, limit=limit)
        if features:
            queries = [_query(product, version, feature) for feature in features]
            return {
                "type": "release",
                "product": product,
                "version": version,
                "specificity": "high",
                "basis": "distinctive change terms extracted from release notes",
                "feature_phrases": features,
                "queries": list(dict.fromkeys(queries))[:limit],
            }
        query = _query(product, version, "release")
        return {
            "type": "release",
            "product": product,
            "version": version,
            "specificity": "low",
            "basis": "release metadata contains no meaningful feature information; version-only intent",
            "feature_phrases": [],
            "queries": [query],
        }

    title = re.sub(r"^(?:Show|Launch) HN:\s*", "", candidate.title, flags=re.I)
    title = _without_repository_syntax(title)
    title_words = [word for word in WORD_RE.findall(title) if word.lower() not in QUERY_STOPWORDS]
    distinctive = " ".join(title_words[:10])
    primary = _query(product, distinctive)
    item_types = {item.item_type for item in candidate.items}
    is_project = any(value in {"github_exploratory_repository", "huggingface_space", "github_new_repository"} for value in item_types)
    queries = [primary]
    if is_project and limit > 1:
        queries.append(_query(product, " ".join(title_words[:6]), "demo"))
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


def _video(details: dict[str, Any], query: str) -> dict[str, Any]:
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
    if disabled or not bool(config.youtube.get("enabled", True)) or not key:
        reason = "disabled by command/configuration" if disabled or not bool(config.youtube.get("enabled", True)) else "YOUTUBE_API_KEY is not set"
        for candidate in targets:
            intent = build_viewer_intent(candidate, int(config.youtube.get("queries_per_candidate", 2)))
            queries = intent["queries"]
            candidate.youtube = {
                "status": "disabled",
                "reason": reason,
                "queries": queries,
                "manual_search_urls": [f"https://www.youtube.com/results?search_query={quote_plus(query)}" for query in queries],
                "videos": [],
                "viewer_intent": intent,
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
        ordered_ids: list[tuple[str, str]] = []
        candidate_failures: list[str] = []
        for query in queries:
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
                        "q": query,
                        "publishedAfter": published_after,
                        "maxResults": results_per_query,
                        "order": "relevance",
                    },
                    ttl=cache_ttl,
                )
                cache_states.append((payload.cache_state, payload.fetched_at))
                known_ids = {value for value, _ in ordered_ids}
                for result in payload.json().get("items", []):
                    video_id = result.get("id", {}).get("videoId")
                    if video_id and video_id not in known_ids:
                        ordered_ids.append((video_id, query))
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
                        "id": ",".join(video_id for video_id, _ in ordered_ids),
                    },
                    ttl=cache_ttl,
                )
                cache_states.append((payload.cache_state, payload.fetched_at))
                by_id = {value["id"]: value for value in payload.json().get("items", [])}
                videos = [_video(by_id[video_id], query) for video_id, query in ordered_ids if video_id in by_id]
            except Exception as exc:
                candidate_failures.append(f"video details: {compact_error(exc, [key])}")

        candidate.youtube = {
            "status": "partial" if candidate_failures else "ok",
            "queries": queries,
            "manual_search_urls": [f"https://www.youtube.com/results?search_query={quote_plus(query)}" for query in queries],
            "videos": videos,
            "viewer_intent": intent,
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
