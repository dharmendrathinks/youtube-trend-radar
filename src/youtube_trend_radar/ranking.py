from __future__ import annotations

from dataclasses import replace
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
        if item.item_type == "github_repository_snapshot":
            created = item.published_at
            if created and max(0.0, (now - created).total_seconds() / 3600) <= maximum_age_hours:
                output.append(
                    replace(
                        item,
                        item_type="github_new_repository",
                        title=f"{item.metrics.get('repo_full_name', item.title)}: new repository — {item.summary}",
                        metrics={**item.metrics, "event_basis": "repository creation time"},
                    )
                )
                continue
            growth = item.metrics.get("observed_growth", {})
            stars = growth.get("metrics", {}).get("stars", {})
            duration = float(growth.get("observation_duration_hours", 0.0) or 0.0)
            delta = float(stars.get("delta", 0.0) or 0.0)
            initial = float(stars.get("initial", 0.0) or 0.0)
            relative_percent = (delta / initial * 100.0) if initial > 0 else (100.0 if delta > 0 else 0.0)
            gate = config.ranking.eligibility
            if (
                growth.get("available")
                and duration >= gate.watched_repo_growth_min_observation_hours
                and delta >= gate.watched_repo_growth_min_star_delta
                and relative_percent >= gate.watched_repo_growth_min_relative_percent
            ):
                repo = str(item.metrics.get("repo_full_name", item.title))
                output.append(
                    replace(
                        item,
                        item_type="github_observed_growth",
                        title=f"{repo}: observed star growth (+{int(delta)} over {duration:.1f}h)",
                        published_at=item.observed_at,
                        metrics={
                            **item.metrics,
                            "event_basis": "configured observed-growth trigger",
                            "observed_star_relative_percent": round(relative_percent, 3),
                        },
                    )
                )
            continue
        age_hours = max(0.0, (now - effective_item_time(item)).total_seconds() / 3600)
        if age_hours > maximum_age_hours:
            continue
        output.append(item)
    return output


def attach_repository_support(candidates: list[Candidate], items: list[SourceItem]) -> None:
    snapshots = {
        str(item.metrics.get("repo_full_name", "")).lower(): item
        for item in items
        if item.item_type == "github_repository_snapshot" and item.metrics.get("repo_full_name")
    }
    for candidate in candidates:
        if any(item.item_type in {"github_new_repository", "github_observed_growth"} for item in candidate.items):
            continue
        repositories = {
            str(item.metrics.get("repo_full_name", "")).lower()
            for item in candidate.items
            if item.metrics.get("repo_full_name")
        }
        for repository in repositories:
            snapshot = snapshots.get(repository)
            if snapshot and all(
                not (item.provider == snapshot.provider and item.external_id == snapshot.external_id)
                for item in candidate.items
            ):
                candidate.items.append(snapshot)


def candidate_is_eligible(candidate: Candidate, config: AppConfig) -> bool:
    if any(item.authority == "official" or item.source_family == "official" for item in candidate.items):
        return True
    if len(candidate.source_families) >= 2:
        return True
    if any(item.item_type in {"github_new_repository", "github_observed_growth"} for item in candidate.items):
        return True

    gate = config.ranking.eligibility
    if candidate.source_families == ["hacker_news"]:
        points = max((int(item.metrics.get("points", 0)) for item in candidate.items), default=0)
        comments = max((int(item.metrics.get("comments", 0)) for item in candidate.items), default=0)
        return points >= gate.community_hn_min_points or comments >= gate.community_hn_min_comments
    if candidate.source_families == ["github"]:
        stars = max(
            (int(item.metrics.get("stars", 0)) for item in candidate.items if item.item_type == "github_exploratory_repository"),
            default=0,
        )
        return stars >= gate.github_explore_min_stars
    if candidate.source_families == ["huggingface"]:
        likes = max((int(item.metrics.get("likes", 0)) for item in candidate.items), default=0)
        ranks = [int(item.metrics["trending_rank"]) for item in candidate.items if item.metrics.get("trending_rank") is not None]
        return likes >= gate.huggingface_min_likes or (ranks and min(ranks) <= gate.huggingface_max_trending_rank)
    return False


def filter_eligible_candidates(candidates: list[Candidate], config: AppConfig) -> list[Candidate]:
    return [candidate for candidate in candidates if candidate_is_eligible(candidate, config)]


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
