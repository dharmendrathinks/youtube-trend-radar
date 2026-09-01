from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
import json
import os
import tempfile

from youtube_trend_radar.config import AppConfig
from youtube_trend_radar.models import Candidate, ProviderResult, isoformat
from youtube_trend_radar.ranking import SCORING_VERSION


SCHEMA_VERSION = "1.0"


def _candidate_dict(candidate: Candidate, now: datetime, unavailable: list[str]) -> dict[str, Any]:
    age_hours = max(0.0, (now - candidate.effective_event_time).total_seconds() / 3600)
    return {
        "fingerprint": candidate.fingerprint,
        "title": candidate.title,
        "entity": candidate.entity,
        "event_time": isoformat(candidate.effective_event_time),
        "age_hours": round(age_hours, 2),
        "discovery_priority": candidate.discovery_priority,
        "discovery_priority_inputs": {
            "freshness": candidate.freshness,
            "evidence_strength": candidate.evidence_value,
            "interest_value": candidate.interest_value,
        },
        "freshness": candidate.freshness,
        "evidence_level": candidate.evidence_level,
        "interest_band": candidate.interest_band,
        "interest_rule": candidate.interest_rule,
        "interest_inputs": candidate.interest_inputs,
        "source_families": candidate.source_families,
        "observed_signals": [item.to_dict() for item in candidate.items],
        "youtube_evidence": candidate.youtube,
        "why_investigate": (
            f"{age_hours:.1f}h old; {candidate.evidence_level}; "
            f"interest is {candidate.interest_band} because {candidate.interest_rule}."
        ),
        "missing_or_uncertain": list(dict.fromkeys([*candidate.missing, *unavailable])),
        "source_links": sorted(
            {item.canonical_url for item in candidate.items}
            | {link for item in candidate.items for link in item.related_links}
        ),
    }


def build_report(
    *,
    scan_id: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    config: AppConfig,
    provider_results: list[ProviderResult],
    candidates: list[Candidate],
) -> dict[str, Any]:
    unavailable = [
        f"{result.provider}: {result.status}" + (f" ({result.error})" if result.error else "")
        for result in provider_results
        if result.status in {"failed", "disabled", "stale"}
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "scoring_version": SCORING_VERSION,
        "configuration_fingerprint": config.fingerprint,
        "scan_id": scan_id,
        "started_at": isoformat(started_at),
        "generated_at": isoformat(completed_at),
        "status": status,
        "product_boundary": "YouTube evidence is not included in Discovery Priority; inspect it manually.",
        "provider_status": [result.status_dict() for result in provider_results],
        "effective_interest_thresholds": asdict(config.ranking.interest),
        "effective_eligibility_thresholds": asdict(config.ranking.eligibility),
        "recommendations": [_candidate_dict(candidate, completed_at, unavailable) for candidate in candidates],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# YouTube Trend Radar",
        "",
        f"Generated: {report['generated_at']}",
        f"Scan: `{report['scan_id']}` · Status: **{report['status']}** · Scoring: `{report['scoring_version']}`",
        "",
        f"> {report['product_boundary']}",
        "",
        "## Provider status",
        "",
        "| Provider | Status | Items | Requests | Detail |",
        "|---|---:|---:|---:|---|",
    ]
    for provider in report["provider_status"]:
        detail = provider.get("error") or (f"stale as of {provider['stale_as_of']}" if provider.get("stale_as_of") else "—")
        safe_detail = str(detail).replace("|", "\\|")
        lines.append(f"| {provider['provider']} | {provider['status']} | {provider['item_count']} | {provider['request_count']} | {safe_detail} |")

    lines.extend(["", "## Top opportunities", ""])
    if not report["recommendations"]:
        lines.extend(["No relevant candidates were found in the configured lookback window.", ""])
    for index, candidate in enumerate(report["recommendations"], start=1):
        entity = f" · {candidate['entity']}" if candidate.get("entity") else ""
        lines.extend(
            [
                f"### {index}. {candidate['title']}",
                "",
                f"**Discovery Priority:** {candidate['discovery_priority']:.2f} · **Freshness:** {candidate['freshness']:.2f} · **Age:** {candidate['age_hours']:.1f}h{entity}",
                "",
                f"**Why investigate:** {candidate['why_investigate']}",
                "",
                f"**Evidence:** {candidate['evidence_level']} · **Interest:** {candidate['interest_band']} (`{candidate['interest_rule']}`)",
                "",
                "Observed signals:",
                "",
            ]
        )
        for signal in candidate["observed_signals"]:
            metric_parts = []
            for key, value in signal["metrics"].items():
                if key == "observed_growth":
                    continue
                if isinstance(value, (str, int, float)) and value not in ("", None):
                    metric_parts.append(f"{key}={value}")
            metrics = f" ({', '.join(metric_parts[:6])})" if metric_parts else ""
            lines.append(f"- [{signal['provider']}] [{signal['title']}]({signal['canonical_url']}) — {signal['published_at'] or signal['observed_at']}{metrics}")

        youtube = candidate["youtube_evidence"]
        lines.extend(["", f"YouTube evidence ({youtube.get('status', 'not checked')} — not included in Discovery Priority):", ""])
        for query, url in zip(youtube.get("queries", []), youtube.get("manual_search_urls", []), strict=False):
            lines.append(f"- Query: [{query}]({url})")
        for video in youtube.get("videos", []):
            views = f" · {video['views']:,} views" if video.get("views") is not None else ""
            lines.append(f"- [{video['title']}]({video['url']}) — {video['channel']} · {video['published_at']}{views}")
        if youtube.get("reason"):
            lines.append(f"- Unavailable: {youtube['reason']}")
        for error in youtube.get("errors", []):
            lines.append(f"- Error: {error}")

        if candidate.get("missing_or_uncertain"):
            lines.extend(["", "Missing or uncertain:", ""])
            lines.extend(f"- {value}" for value in candidate["missing_or_uncertain"])
        lines.append("")

    lines.extend(
        [
            "## Scoring method",
            "",
            "`Discovery Priority = 0.60 × Freshness + 0.25 × Evidence Strength + 0.15 × Interest Value`",
            "",
            "Eligibility and interest thresholds are configuration-driven starting heuristics. The JSON report records their effective values and raw inputs.",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_reports(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    stamp = str(report["generated_at"]).replace("-", "").replace(":", "").replace("+", "")
    base = f"scan-{stamp}-{report['scan_id']}"
    json_path = directory / f"{base}.json"
    markdown_path = directory / f"{base}.md"
    json_content = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    markdown_content = render_markdown(report)
    _atomic_write(json_path, json_content)
    _atomic_write(markdown_path, markdown_content)
    _atomic_write(directory / "latest.json", json_content)
    _atomic_write(directory / "latest.md", markdown_content)
    return markdown_path, json_path
