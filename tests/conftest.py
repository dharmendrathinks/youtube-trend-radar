from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from youtube_trend_radar.config import AppConfig, load_config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    loaded = load_config(ROOT / "config.example.toml")
    return replace(loaded, database_path=tmp_path / "radar.sqlite3", reports_path=tmp_path / "reports")

