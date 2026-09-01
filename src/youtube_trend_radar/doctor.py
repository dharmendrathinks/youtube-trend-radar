from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import os

from huggingface_hub import HfApi

from youtube_trend_radar.config import ConfigError, load_config
from youtube_trend_radar.db import Database
from youtube_trend_radar.http import CachedHttpClient
from youtube_trend_radar.providers import github
from youtube_trend_radar.utils import compact_error


def run_doctor(config_path: Path) -> int:
    checks: list[tuple[str, str, str]] = []
    failed = False
    try:
        config = load_config(config_path)
        checks.append(("configuration", "ok", str(config.source_path)))
        database = Database(config.database_path)
        database.initialize()
        database.healthcheck()
        checks.append(("sqlite", "ok", str(config.database_path)))
    except (ConfigError, OSError, ValueError) as exc:
        print(f"FAIL configuration/storage: {compact_error(exc)}")
        return 1

    probes = [
        ("official feeds", config.official_feeds[0].url if config.official_feeds else None),
        ("Hacker News", "https://hacker-news.firebaseio.com/v0/maxitem.json"),
    ]
    for name, url in probes:
        if not url:
            checks.append((name, "warn", "not configured"))
            continue
        try:
            CachedHttpClient(database, config.http).get(url, ttl=timedelta(minutes=5))
            checks.append((name, "ok", "reachable"))
        except Exception as exc:
            failed = True
            checks.append((name, "fail", compact_error(exc)))

    try:
        github_client = github.build_client(config, database)
        payload = github_client.get("https://api.github.com/rate_limit", ttl=timedelta(minutes=5))
        remaining = payload.json().get("resources", {}).get("core", {}).get("remaining", "unknown")
        credential = "token configured" if os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") else "anonymous"
        checks.append(("GitHub", "ok", f"{credential}; core remaining {remaining}"))
    except Exception as exc:
        failed = True
        checks.append(("GitHub", "fail", compact_error(exc, [os.getenv("GITHUB_TOKEN", ""), os.getenv("GH_TOKEN", "")])))

    hf_token = os.getenv("HF_TOKEN")
    try:
        next(iter(HfApi(token=hf_token or None).list_models(limit=1)))
        checks.append(("Hugging Face", "ok", "token configured" if hf_token else "public access"))
    except Exception as exc:
        failed = True
        checks.append(("Hugging Face", "fail", compact_error(exc, [hf_token] if hf_token else [])))

    youtube_key = os.getenv("YOUTUBE_API_KEY")
    if not youtube_key:
        checks.append(("YouTube", "warn", "YOUTUBE_API_KEY not set; scans will show manual links only"))
    else:
        try:
            client = CachedHttpClient(database, config.http, secrets=[youtube_key])
            client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"key": youtube_key, "part": "id", "id": "dQw4w9WgXcQ"},
                ttl=timedelta(minutes=5),
            )
            checks.append(("YouTube", "ok", "API key accepted"))
        except Exception as exc:
            failed = True
            checks.append(("YouTube", "fail", compact_error(exc, [youtube_key])))

    width = max(len(name) for name, _, _ in checks)
    for name, status, detail in checks:
        print(f"{status.upper():4} {name:<{width}}  {detail}")
    return 1 if failed else 0

