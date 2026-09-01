from __future__ import annotations

from pathlib import Path

import pytest

from youtube_trend_radar.config import ConfigError, load_config
from youtube_trend_radar.utils import clean_text, normalize_url, parse_datetime


ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads_with_external_interest_thresholds() -> None:
    config = load_config(ROOT / "config.example.toml")
    assert config.ranking.interest.strong_hn_points == 100
    assert config.ranking.interest.moderate_github_observed_star_delta == 5
    assert config.ranking.eligibility.community_hn_min_points == 5
    assert config.ranking.eligibility.watched_repo_growth_min_observation_hours == 24
    assert config.fingerprint == load_config(ROOT / "config.example.toml").fingerprint


def test_weights_must_sum_to_one(tmp_path: Path) -> None:
    content = (ROOT / "config.example.toml").read_text().replace("freshness_weight = 0.60", "freshness_weight = 0.61")
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(ConfigError, match="sum to 1.0"):
        load_config(path)


def test_normalization_helpers() -> None:
    assert normalize_url("https://www.Example.com/path/?utm_source=x&b=2&a=1#frag") == "https://example.com/path?a=1&b=2"
    assert clean_text("<p>Hello&nbsp; world</p>") == "Hello world"
    assert parse_datetime("2026-01-02T03:04:05Z").isoformat() == "2026-01-02T03:04:05+00:00"
