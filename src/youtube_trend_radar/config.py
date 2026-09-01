from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import hashlib
import json
import tomllib


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


@dataclass(slots=True)
class OfficialFeedConfig:
    name: str
    url: str
    entity: str | None = None


@dataclass(slots=True)
class HttpConfig:
    timeout_seconds: float = 15.0
    cache_ttl_minutes: int = 30
    stale_if_error_hours: int = 48
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    user_agent: str = "youtube-trend-radar/0.1"


@dataclass(slots=True)
class InterestConfig:
    strong_hn_points: int = 100
    strong_hn_comments: int = 50
    moderate_hn_points: int = 20
    moderate_hn_comments: int = 10
    strong_github_observed_star_delta: int = 50
    moderate_github_observed_star_delta: int = 5
    exploratory_repo_max_age_days: int = 30
    strong_exploratory_repo_stars: int = 500
    moderate_exploratory_repo_stars: int = 50
    strong_huggingface_likes: int = 100
    moderate_huggingface_likes: int = 20
    strong_huggingface_trending_rank: int = 25
    moderate_huggingface_trending_rank: int = 100
    strong_source_family_count: int = 3
    moderate_source_family_count: int = 2


@dataclass(slots=True)
class EligibilityConfig:
    community_hn_min_points: int = 5
    community_hn_min_comments: int = 2
    github_explore_min_stars: int = 10
    huggingface_min_likes: int = 10
    huggingface_max_trending_rank: int = 100
    watched_repo_growth_min_observation_hours: int = 24
    watched_repo_growth_min_star_delta: int = 50
    watched_repo_growth_min_relative_percent: float = 0.5
    community_language_min_letters: int = 20
    community_min_latin_letter_ratio: float = 0.6
    community_hn_stagnation_hours: float = 3.0
    main_list_min_freshness: float = 40.0


@dataclass(slots=True)
class RankingConfig:
    freshness_half_life_hours: float = 48.0
    freshness_weight: float = 0.60
    evidence_weight: float = 0.25
    interest_weight: float = 0.15
    interest: InterestConfig = field(default_factory=InterestConfig)
    eligibility: EligibilityConfig = field(default_factory=EligibilityConfig)


@dataclass(slots=True)
class DedupConfig:
    max_time_distance_hours: int = 72
    anchored_title_similarity: float = 0.55
    feature_anchors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AppConfig:
    source_path: Path
    database_path: Path
    reports_path: Path
    lookback_days: int
    top_results: int
    max_workers: int
    http: HttpConfig
    official_feeds: list[OfficialFeedConfig]
    github: dict[str, Any]
    hacker_news: dict[str, Any]
    huggingface: dict[str, Any]
    youtube: dict[str, Any]
    topics: dict[str, Any]
    ranking: RankingConfig
    deduplication: DedupConfig
    entities: dict[str, list[str]]
    relevance: dict[str, list[str]]
    categories: dict[str, list[str]]
    raw: dict[str, Any]

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.raw, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _require_table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a TOML table")
    return value


def _positive(value: Any, name: str, *, allow_zero: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{name} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def load_config(path: str | Path) -> AppConfig:
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise ConfigError(
            f"configuration not found: {source_path}. "
            "Copy config.example.toml to config.toml first."
        )

    with source_path.open("rb") as handle:
        data = tomllib.load(handle)

    scan = _require_table(data, "scan")
    paths = _require_table(data, "paths")
    http_raw = _require_table(data, "http")
    official = _require_table(data, "official")
    github = _require_table(data, "github")
    hn = _require_table(data, "hacker_news")
    hf = _require_table(data, "huggingface")
    youtube = _require_table(data, "youtube")
    topics = _require_table(data, "topics")
    ranking_raw = _require_table(data, "ranking")
    interest_raw = ranking_raw.get("interest", {})
    if not isinstance(interest_raw, dict):
        raise ConfigError("[ranking.interest] must be a TOML table")
    eligibility_raw = ranking_raw.get("eligibility", {})
    if not isinstance(eligibility_raw, dict):
        raise ConfigError("[ranking.eligibility] must be a TOML table")
    dedup_raw = _require_table(data, "deduplication")

    feeds: list[OfficialFeedConfig] = []
    for entry in official.get("feeds", []):
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("url"):
            raise ConfigError("every [[official.feeds]] entry needs name and url")
        feeds.append(OfficialFeedConfig(str(entry["name"]), str(entry["url"]), entry.get("entity")))

    interest_values = {
        item.name: _positive(interest_raw.get(item.name, item.default), f"ranking.interest.{item.name}", allow_zero=True)
        for item in InterestConfig.__dataclass_fields__.values()
    }
    interest = InterestConfig(**interest_values)
    eligibility = EligibilityConfig(
        community_hn_min_points=_positive(eligibility_raw.get("community_hn_min_points", 5), "ranking.eligibility.community_hn_min_points", allow_zero=True),
        community_hn_min_comments=_positive(eligibility_raw.get("community_hn_min_comments", 2), "ranking.eligibility.community_hn_min_comments", allow_zero=True),
        github_explore_min_stars=_positive(eligibility_raw.get("github_explore_min_stars", 10), "ranking.eligibility.github_explore_min_stars", allow_zero=True),
        huggingface_min_likes=_positive(eligibility_raw.get("huggingface_min_likes", 10), "ranking.eligibility.huggingface_min_likes", allow_zero=True),
        huggingface_max_trending_rank=_positive(eligibility_raw.get("huggingface_max_trending_rank", 100), "ranking.eligibility.huggingface_max_trending_rank"),
        watched_repo_growth_min_observation_hours=_positive(eligibility_raw.get("watched_repo_growth_min_observation_hours", 24), "ranking.eligibility.watched_repo_growth_min_observation_hours"),
        watched_repo_growth_min_star_delta=_positive(eligibility_raw.get("watched_repo_growth_min_star_delta", 50), "ranking.eligibility.watched_repo_growth_min_star_delta", allow_zero=True),
        watched_repo_growth_min_relative_percent=float(eligibility_raw.get("watched_repo_growth_min_relative_percent", 0.5)),
        community_language_min_letters=_positive(
            eligibility_raw.get("community_language_min_letters", 20),
            "ranking.eligibility.community_language_min_letters",
        ),
        community_min_latin_letter_ratio=float(eligibility_raw.get("community_min_latin_letter_ratio", 0.6)),
        community_hn_stagnation_hours=float(eligibility_raw.get("community_hn_stagnation_hours", 3.0)),
        main_list_min_freshness=float(eligibility_raw.get("main_list_min_freshness", 40.0)),
    )
    if eligibility.watched_repo_growth_min_relative_percent < 0:
        raise ConfigError("ranking.eligibility.watched_repo_growth_min_relative_percent must be >= 0")
    if not 0 <= eligibility.community_min_latin_letter_ratio <= 1:
        raise ConfigError("ranking.eligibility.community_min_latin_letter_ratio must be between 0 and 1")
    if eligibility.community_hn_stagnation_hours <= 0:
        raise ConfigError("ranking.eligibility.community_hn_stagnation_hours must be positive")
    if not 0 <= eligibility.main_list_min_freshness <= 100:
        raise ConfigError("ranking.eligibility.main_list_min_freshness must be between 0 and 100")
    ranking = RankingConfig(
        freshness_half_life_hours=float(ranking_raw.get("freshness_half_life_hours", 48.0)),
        freshness_weight=float(ranking_raw.get("freshness_weight", 0.60)),
        evidence_weight=float(ranking_raw.get("evidence_weight", 0.25)),
        interest_weight=float(ranking_raw.get("interest_weight", 0.15)),
        interest=interest,
        eligibility=eligibility,
    )
    if ranking.freshness_half_life_hours <= 0:
        raise ConfigError("ranking.freshness_half_life_hours must be positive")
    if abs(ranking.freshness_weight + ranking.evidence_weight + ranking.interest_weight - 1.0) > 1e-6:
        raise ConfigError("ranking weights must sum to 1.0")

    base = source_path.parent
    database_path = (base / str(paths.get("database", "data/radar.sqlite3"))).resolve()
    reports_path = (base / str(paths.get("reports", "reports"))).resolve()

    entities = _require_table(data, "entities")
    relevance = _require_table(data, "relevance")
    categories = _require_table(data, "categories")
    for section_name, section in (("entities", entities), ("relevance", relevance), ("categories", categories)):
        if not all(isinstance(values, list) for values in section.values()):
            raise ConfigError(f"all [{section_name}] values must be arrays")

    return AppConfig(
        source_path=source_path,
        database_path=database_path,
        reports_path=reports_path,
        lookback_days=_positive(scan.get("lookback_days", 7), "scan.lookback_days"),
        top_results=_positive(scan.get("top_results", 10), "scan.top_results"),
        max_workers=_positive(scan.get("max_workers", 5), "scan.max_workers"),
        http=HttpConfig(
            timeout_seconds=float(http_raw.get("timeout_seconds", 15.0)),
            cache_ttl_minutes=_positive(http_raw.get("cache_ttl_minutes", 30), "http.cache_ttl_minutes"),
            stale_if_error_hours=_positive(http_raw.get("stale_if_error_hours", 48), "http.stale_if_error_hours"),
            max_retries=_positive(http_raw.get("max_retries", 2), "http.max_retries", allow_zero=True),
            retry_backoff_seconds=float(http_raw.get("retry_backoff_seconds", 0.5)),
            user_agent=str(http_raw.get("user_agent", "youtube-trend-radar/0.1")),
        ),
        official_feeds=feeds,
        github=github,
        hacker_news=hn,
        huggingface=hf,
        youtube=youtube,
        topics=topics,
        ranking=ranking,
        deduplication=DedupConfig(
            max_time_distance_hours=_positive(dedup_raw.get("max_time_distance_hours", 72), "deduplication.max_time_distance_hours"),
            anchored_title_similarity=float(dedup_raw.get("anchored_title_similarity", 0.55)),
            feature_anchors=[str(value).lower() for value in dedup_raw.get("feature_anchors", [])],
        ),
        entities={str(key): [str(value).lower() for value in values] for key, values in entities.items()},
        relevance={str(key): [str(value).lower() for value in values] for key, values in relevance.items()},
        categories={str(key): [str(value).lower() for value in values] for key, values in categories.items()},
        raw=data,
    )
