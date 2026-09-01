from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from youtube_trend_radar.models import ProviderResult, SourceItem
from youtube_trend_radar import pipeline


ROOT = Path(__file__).resolve().parents[1]


def test_mocked_end_to_end_scan_isolates_provider_failure(tmp_path: Path, monkeypatch) -> None:
    config_text = (ROOT / "config.example.toml").read_text()
    config_text = config_text.replace('database = "data/radar.sqlite3"', 'database = "radar.sqlite3"')
    config_text = config_text.replace('reports = "reports"', 'reports = "output"')
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    now = datetime.now(UTC)
    item = SourceItem(
        provider="official",
        external_id="release",
        source_family="official",
        item_type="official_announcement",
        title="Codex background agents released",
        summary="New AI coding workflow",
        canonical_url="https://openai.com/codex/background-agents",
        published_at=now,
        updated_at=None,
        observed_at=now,
        entity="OpenAI",
        authority="official",
    )

    monkeypatch.setattr(pipeline.official, "collect", lambda *args: ProviderResult("official", "ok", [item], now))
    monkeypatch.setattr(pipeline.github, "collect_watched", lambda *args: (_ for _ in ()).throw(RuntimeError("GitHub unavailable")))
    monkeypatch.setattr(pipeline.github, "collect_exploratory", lambda *args: ProviderResult("github_explore", "ok", [], now))
    monkeypatch.setattr(pipeline.hackernews, "collect", lambda *args: ProviderResult("hacker_news", "ok", [], now))
    monkeypatch.setattr(pipeline.huggingface, "collect", lambda *args: ProviderResult("huggingface", "ok", [], now))

    def disable_youtube(candidates, config, client, when, *, disabled=False):
        for candidate in candidates:
            candidate.youtube = {"status": "disabled", "queries": [], "manual_search_urls": [], "videos": [], "included_in_discovery_priority": False}
        return ProviderResult("youtube", "disabled", [], when, error="test")

    monkeypatch.setattr(pipeline.youtube, "validate", disable_youtube)
    assert pipeline.run_scan(config_path) == 0
    reports = list((tmp_path / "output").glob("scan-*.json"))
    assert len(reports) == 1
    content = reports[0].read_text()
    assert '"discovery_priority"' in content
    assert '"github_watched"' in content
    assert '"status": "failed"' in content

