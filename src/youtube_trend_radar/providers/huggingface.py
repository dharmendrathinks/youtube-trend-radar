from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable
import os

from huggingface_hub import HfApi

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.models import ProviderResult, SourceItem
from youtube_trend_radar.utils import clean_text, compact_error, get_value, normalize_url, parse_datetime


def _to_item(value: Any, *, kind: str, now: datetime, rank: int) -> SourceItem:
    identifier = str(get_value(value, "id") or get_value(value, "modelId"))
    url = f"https://huggingface.co/{'spaces/' if kind == 'space' else ''}{identifier}"
    tags = [str(tag).lower() for tag in (get_value(value, "tags", []) or [])]
    likes = int(get_value(value, "likes", 0) or 0)
    downloads = int(get_value(value, "downloads", 0) or 0)
    trending_score = get_value(value, "trending_score", get_value(value, "trendingScore"))
    summary = f"Hugging Face {kind}; tags: {', '.join(tags[:12])}"
    return SourceItem(
        provider="huggingface",
        external_id=f"{kind}:{identifier}",
        source_family="huggingface",
        item_type=f"huggingface_{kind}",
        title=identifier,
        summary=clean_text(summary),
        canonical_url=normalize_url(url),
        published_at=parse_datetime(get_value(value, "created_at", get_value(value, "createdAt"))),
        updated_at=parse_datetime(get_value(value, "last_modified", get_value(value, "lastModified"))),
        observed_at=now,
        entity=identifier.split("/", 1)[0] if "/" in identifier else None,
        categories=tags,
        metrics={
            "likes": likes,
            "downloads": downloads,
            "trending_rank": rank,
            "trending_score": trending_score,
            "kind": kind,
        },
    )


def collect(config: AppConfig, now: datetime) -> ProviderResult:
    token = os.getenv("HF_TOKEN") or None
    api = HfApi(token=token)
    items: list[SourceItem] = []
    failures: list[str] = []
    requests = 0

    sources: list[tuple[str, int, Any]] = [
        ("model", int(config.huggingface.get("models_limit", 40)), api.list_models),
        ("space", int(config.huggingface.get("spaces_limit", 30)), api.list_spaces),
    ]
    for kind, limit, method in sources:
        try:
            requests += 1
            values: Iterable[Any] = method(sort="trending_score", limit=limit)
            items.extend(_to_item(value, kind=kind, now=now, rank=rank) for rank, value in enumerate(values, start=1))
        except Exception as exc:
            failures.append(f"{kind}s: {compact_error(exc, [token] if token else [])}")

    if not items and failures:
        status = "failed"
    elif failures:
        status = "partial"
    else:
        status = "ok"
    return ProviderResult(
        provider="huggingface",
        status=status,
        items=items,
        fetched_at=now,
        error="; ".join(failures) or None,
        request_count=requests,
    )

