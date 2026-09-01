from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


ProviderStatus = Literal["ok", "partial", "cached", "stale", "failed", "disabled"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


@dataclass(slots=True)
class SourceItem:
    provider: str
    external_id: str
    source_family: str
    item_type: str
    title: str
    summary: str
    canonical_url: str
    published_at: datetime | None
    updated_at: datetime | None
    observed_at: datetime
    entity: str | None = None
    categories: list[str] = field(default_factory=list)
    authority: str = "community"
    metrics: dict[str, Any] = field(default_factory=dict)
    related_links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "external_id": self.external_id,
            "source_family": self.source_family,
            "item_type": self.item_type,
            "title": self.title,
            "summary": self.summary,
            "canonical_url": self.canonical_url,
            "published_at": isoformat(self.published_at),
            "updated_at": isoformat(self.updated_at),
            "observed_at": isoformat(self.observed_at),
            "entity": self.entity,
            "categories": self.categories,
            "authority": self.authority,
            "metrics": self.metrics,
            "related_links": self.related_links,
        }


@dataclass(slots=True)
class ProviderResult:
    provider: str
    status: ProviderStatus
    items: list[SourceItem]
    fetched_at: datetime
    stale_as_of: datetime | None = None
    error: str | None = None
    request_count: int = 0

    def status_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "item_count": len(self.items),
            "fetched_at": isoformat(self.fetched_at),
            "stale_as_of": isoformat(self.stale_as_of),
            "error": self.error,
            "request_count": self.request_count,
        }


@dataclass(slots=True)
class Candidate:
    fingerprint: str
    title: str
    entity: str | None
    effective_event_time: datetime
    items: list[SourceItem]
    source_families: list[str]
    freshness: float = 0.0
    evidence_level: str = "single-source"
    evidence_value: int = 55
    interest_band: str = "early/limited"
    interest_value: int = 25
    interest_rule: str = "no configured threshold met"
    interest_inputs: dict[str, Any] = field(default_factory=dict)
    discovery_priority: float = 0.0
    video_topic: dict[str, Any] = field(default_factory=dict)
    youtube: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
