from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from urllib.parse import urlsplit
import re

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.models import Candidate, SourceItem
from youtube_trend_radar.utils import normalize_url


TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+){0,2}(?:[-._][a-z0-9]+)?\b", re.I)
MODEL_RE = re.compile(r"\b(?:gpt|claude|gemini|llama|qwen|mistral|deepseek)[-\s]?[a-z]*\d[\w.-]*\b", re.I)
STOPWORDS = {
    "a", "an", "and", "for", "from", "in", "is", "new", "of", "on", "release",
    "released", "the", "to", "update", "with", "ai", "agent", "model",
}


def normalized_tokens(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.lower()) if token not in STOPWORDS and len(token) > 1}


def normalized_title(text: str) -> str:
    return " ".join(TOKEN_RE.findall(text.lower()))


def resolve_items(items: list[SourceItem], config: AppConfig) -> None:
    alias_pairs = sorted(
        ((alias, entity) for entity, aliases in config.entities.items() for alias in [entity.lower(), *aliases]),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    for item in items:
        text = f"{item.title} {item.summary} {item.canonical_url} {' '.join(item.categories)}".lower()
        existing = (item.entity or "").lower()
        for alias, entity in alias_pairs:
            if alias == existing or alias in text:
                item.entity = entity
                break
        matched_categories = set(item.categories)
        for category, terms in config.categories.items():
            if any(term in text for term in terms):
                matched_categories.add(category)
        item.categories = sorted(matched_categories)


def is_relevant(item: SourceItem, config: AppConfig) -> bool:
    text = f"{item.title} {item.summary} {item.canonical_url} {' '.join(item.categories)}".lower()
    if any(term in text for term in config.relevance.get("exclude_terms", [])):
        return False
    watched_entity = item.entity in config.entities
    if watched_entity:
        return True
    ai_match = any(term in text for term in config.relevance.get("ai_terms", []))
    developer_match = any(term in text for term in config.relevance.get("developer_terms", []))
    if ai_match and developer_match:
        return True
    if item.source_family == "huggingface":
        return any(term in text for term in config.relevance.get("huggingface_terms", []))
    return False


def effective_item_time(item: SourceItem) -> datetime:
    if item.item_type == "github_repository_snapshot":
        return item.observed_at
    if item.item_type == "github_exploratory_repository":
        return item.published_at or item.observed_at
    if item.source_family == "huggingface":
        return item.updated_at or item.published_at or item.observed_at
    return item.published_at or item.updated_at or item.observed_at


def event_anchors(item: SourceItem, config: AppConfig) -> set[str]:
    text = f"{item.title} {item.summary}".lower()
    anchors = {f"version:{match.lower()}" for match in VERSION_RE.findall(text)}
    anchors.update(f"model:{match.lower().replace(' ', '-')}" for match in MODEL_RE.findall(text))
    repo = item.metrics.get("repo_full_name")
    if repo:
        anchors.add(f"repo:{str(repo).lower()}")
    tag = item.metrics.get("release_tag")
    if repo and tag:
        anchors.add(f"release:{str(repo).lower()}@{str(tag).lower()}")
    for feature in config.deduplication.feature_anchors:
        if feature in text:
            anchors.add(f"feature:{feature}")
    return anchors


def _jaccard(left: str, right: str) -> float:
    a, b = normalized_tokens(left), normalized_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def should_merge(left: SourceItem, right: SourceItem, config: AppConfig) -> bool:
    left_url, right_url = normalize_url(left.canonical_url), normalize_url(right.canonical_url)
    if left_url and left_url == right_url:
        return True

    left_repo, right_repo = left.metrics.get("repo_full_name"), right.metrics.get("repo_full_name")
    left_tag, right_tag = left.metrics.get("release_tag"), right.metrics.get("release_tag")
    if left_repo and left_tag and str(left_repo).lower() == str(right_repo).lower() and str(left_tag).lower() == str(right_tag).lower():
        return True

    delta_hours = abs((effective_item_time(left) - effective_item_time(right)).total_seconds()) / 3600
    if delta_hours > config.deduplication.max_time_distance_hours:
        return False
    if left.entity and right.entity and left.entity != right.entity:
        return False
    if normalized_title(left.title) == normalized_title(right.title) and normalized_title(left.title):
        return True

    shared = event_anchors(left, config) & event_anchors(right, config)
    if not shared:
        return False
    return _jaccard(left.title, right.title) >= config.deduplication.anchored_title_similarity


def _candidate_title(items: list[SourceItem]) -> str:
    official = [item for item in items if item.authority == "official"]
    pool = official or items
    return max(pool, key=lambda item: (len(normalized_tokens(item.title)), len(item.title))).title


def _fingerprint(items: list[SourceItem], config: AppConfig) -> str:
    anchors = sorted(set().union(*(event_anchors(item, config) for item in items)))
    canonical = sorted(normalize_url(item.canonical_url) for item in items if item.canonical_url)
    identity = anchors[0] if anchors else (canonical[0] if canonical else normalized_title(_candidate_title(items)))
    entity = next((item.entity for item in items if item.entity), "unknown")
    return sha256(f"{entity}|{identity}".encode()).hexdigest()[:16]


def cluster_items(items: list[SourceItem], config: AppConfig) -> list[Candidate]:
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(items)):
        for right in range(left + 1, len(items)):
            if should_merge(items[left], items[right], config):
                union(left, right)

    groups: dict[int, list[SourceItem]] = defaultdict(list)
    for index, item in enumerate(items):
        groups[find(index)].append(item)

    candidates: list[Candidate] = []
    for group in groups.values():
        ordered = sorted(group, key=effective_item_time)
        entities = [item.entity for item in ordered if item.entity]
        candidates.append(
            Candidate(
                fingerprint=_fingerprint(ordered, config),
                title=_candidate_title(ordered),
                entity=entities[0] if entities else None,
                effective_event_time=effective_item_time(ordered[0]),
                items=ordered,
                source_families=sorted({item.source_family for item in ordered}),
            )
        )
    return candidates

