from __future__ import annotations

from datetime import datetime
from math import pow
from typing import Any

from youtube_trend_radar.config import AppConfig, InterestConfig
from youtube_trend_radar.models import Candidate, SourceItem
from youtube_trend_radar.resolution import effective_item_time, is_relevant, resolve_items


SCORING_VERSION = "v1.0"


def observed_delta(item: SourceItem, metric: str) -> float | None:
    growth = item.metrics.get("observed_growth", {})
    if not growth.get("available"):
        return None
    details = growth.get("metrics", {}).get(metric)
    return float(details["delta"]) if details else None


def eligible_items(items: list[SourceItem], config: AppConfig, now: datetime) -> list[SourceItem]:
    resolve_items(items, config)
    output: list[SourceItem] = []
    maximum_age_hours = config.lookback_days * 24
    for item in items:
        if not is_relevant(item, config):
            continue
        age_hours = max(0.0, (now - effective_item_time(item)).total_seconds() / 3600)
        if age_hours > maximum_age_hours:
            continue
        if item.item_type == "github_repository_snapshot":
            delta = observed_delta(item, "stars")
            if delta is None or delta < config.ranking.interest.moderate_github_observed_star_delta:
                continue
        output.append(item)
    return output


def freshness_score(candidate: Candidate, config: AppConfig, now: datetime) -> float:
    age_hours = max(0.0, (now - candidate.effective_event_time).total_seconds() / 3600)
    return 100.0 * pow(2.0, -age_hours / config.ranking.freshness_half_life_hours)


def evidence(candidate: Candidate) -> tuple[str, int]:
    official = any(item.authority == "official" or item.source_family == "official" for item in candidate.items)
    if official and len(candidate.source_families) >= 2:
        return "authoritative + independently confirmed", 100
    if official:
        return "authoritative primary source", 90
    if len(candidate.source_families) >= 2:
        return "multiple independent source families", 80
    return "single non-official source family", 55


def _candidate_metrics(candidate: Candidate, now: datetime) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "hn_points": None,
        "hn_comments": None,
        "github_observed_star_delta": None,
        "exploratory_repo_stars": None,
        "exploratory_repo_age_days": None,
        "huggingface_likes": None,
        "huggingface_trending_rank": None,
        "source_family_count": len(candidate.source_families),
    }
    for item in candidate.items:
        if item.source_family == "hacker_news":
            metrics["hn_points"] = max(metrics["hn_points"] or 0, int(item.metrics.get("points", 0)))
            metrics["hn_comments"] = max(metrics["hn_comments"] or 0, int(item.metrics.get("comments", 0)))
        if item.source_family == "github":
            delta = observed_delta(item, "stars")
            if delta is not None:
                metrics["github_observed_star_delta"] = max(metrics["github_observed_star_delta"] or 0, delta)
        if item.item_type == "github_exploratory_repository":
            metrics["exploratory_repo_stars"] = max(metrics["exploratory_repo_stars"] or 0, int(item.metrics.get("stars", 0)))
            created = item.published_at
            if created:
                age = max(0.0, (now - created).total_seconds() / 86400)
                current = metrics["exploratory_repo_age_days"]
                metrics["exploratory_repo_age_days"] = min(current, age) if current is not None else age
        if item.source_family == "huggingface":
            metrics["huggingface_likes"] = max(metrics["huggingface_likes"] or 0, int(item.metrics.get("likes", 0)))
            rank = item.metrics.get("trending_rank")
            if rank is not None:
                current = metrics["huggingface_trending_rank"]
                metrics["huggingface_trending_rank"] = min(current, int(rank)) if current is not None else int(rank)
    return metrics


def interest(candidate: Candidate, config: InterestConfig, now: datetime) -> tuple[str, int, str, dict[str, Any]]:
    values = _candidate_metrics(candidate, now)
    strong_checks: list[tuple[bool, str]] = [
        (values["hn_points"] is not None and values["hn_points"] >= config.strong_hn_points, f"HN points >= {config.strong_hn_points}"),
        (values["hn_comments"] is not None and values["hn_comments"] >= config.strong_hn_comments, f"HN comments >= {config.strong_hn_comments}"),
        (values["github_observed_star_delta"] is not None and values["github_observed_star_delta"] >= config.strong_github_observed_star_delta, f"observed GitHub star delta >= {config.strong_github_observed_star_delta}"),
        (values["exploratory_repo_stars"] is not None and values["exploratory_repo_age_days"] is not None and values["exploratory_repo_age_days"] <= config.exploratory_repo_max_age_days and values["exploratory_repo_stars"] >= config.strong_exploratory_repo_stars, f"new exploratory repo stars >= {config.strong_exploratory_repo_stars}"),
        (values["huggingface_likes"] is not None and values["huggingface_likes"] >= config.strong_huggingface_likes, f"Hugging Face likes >= {config.strong_huggingface_likes}"),
        (values["huggingface_trending_rank"] is not None and values["huggingface_trending_rank"] <= config.strong_huggingface_trending_rank, f"Hugging Face trending rank <= {config.strong_huggingface_trending_rank}"),
        (values["source_family_count"] >= config.strong_source_family_count, f"source families >= {config.strong_source_family_count}"),
    ]
    moderate_checks: list[tuple[bool, str]] = [
        (values["hn_points"] is not None and values["hn_points"] >= config.moderate_hn_points, f"HN points >= {config.moderate_hn_points}"),
        (values["hn_comments"] is not None and values["hn_comments"] >= config.moderate_hn_comments, f"HN comments >= {config.moderate_hn_comments}"),
        (values["github_observed_star_delta"] is not None and values["github_observed_star_delta"] >= config.moderate_github_observed_star_delta, f"observed GitHub star delta >= {config.moderate_github_observed_star_delta}"),
        (values["exploratory_repo_stars"] is not None and values["exploratory_repo_age_days"] is not None and values["exploratory_repo_age_days"] <= config.exploratory_repo_max_age_days and values["exploratory_repo_stars"] >= config.moderate_exploratory_repo_stars, f"new exploratory repo stars >= {config.moderate_exploratory_repo_stars}"),
        (values["huggingface_likes"] is not None and values["huggingface_likes"] >= config.moderate_huggingface_likes, f"Hugging Face likes >= {config.moderate_huggingface_likes}"),
        (values["huggingface_trending_rank"] is not None and values["huggingface_trending_rank"] <= config.moderate_huggingface_trending_rank, f"Hugging Face trending rank <= {config.moderate_huggingface_trending_rank}"),
        (values["source_family_count"] >= config.moderate_source_family_count, f"source families >= {config.moderate_source_family_count}"),
    ]
    for matched, rule in strong_checks:
        if matched:
            return "strong", 100, rule, values
    for matched, rule in moderate_checks:
        if matched:
            return "moderate", 60, rule, values
    return "early/limited", 25, "no configured interest threshold met", values


def rank_candidates(candidates: list[Candidate], config: AppConfig, now: datetime) -> list[Candidate]:
    for candidate in candidates:
        candidate.freshness = round(freshness_score(candidate, config, now), 2)
        candidate.evidence_level, candidate.evidence_value = evidence(candidate)
        band, value, rule, raw = interest(candidate, config.ranking.interest, now)
        candidate.interest_band = band
        candidate.interest_value = value
        candidate.interest_rule = rule
        candidate.interest_inputs = raw
        candidate.discovery_priority = round(
            config.ranking.freshness_weight * candidate.freshness
            + config.ranking.evidence_weight * candidate.evidence_value
            + config.ranking.interest_weight * candidate.interest_value,
            2,
        )
    return sorted(
        candidates,
        key=lambda candidate: (candidate.discovery_priority, candidate.effective_event_time, candidate.fingerprint),
        reverse=True,
    )
