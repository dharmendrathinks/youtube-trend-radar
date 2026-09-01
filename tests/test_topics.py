from __future__ import annotations

from datetime import UTC, datetime, timedelta

from youtube_trend_radar.models import Candidate, SourceItem
from youtube_trend_radar.topics import (
    attach_video_topics,
    extract_release_topic,
    partition_topicable_candidates,
)


NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def release_candidate(summary: str, *, repository: str = "openai/codex") -> Candidate:
    version = "v1.2.3"
    item = SourceItem(
        provider="github_watched",
        external_id=f"{repository}@{version}",
        source_family="github",
        item_type="github_release",
        title=f"{repository} {version}",
        summary=summary,
        canonical_url=f"https://github.com/{repository}/releases/tag/{version}",
        published_at=NOW - timedelta(hours=1),
        updated_at=None,
        observed_at=NOW,
        entity="OpenAI",
        authority="official",
        metrics={"repo_full_name": repository, "release_tag": version},
    )
    return Candidate(
        fingerprint=f"release:{repository}:{version}",
        title=item.title,
        entity=item.entity,
        effective_event_time=item.published_at,
        items=[item],
        source_families=["github"],
        freshness=98.0,
        evidence_level="authoritative primary source",
        evidence_value=90,
        interest_band="early/limited",
        interest_value=25,
        discovery_priority=85.05,
    )


def test_release_with_one_meaningful_feature_has_grounded_primary_angle() -> None:
    topic = extract_release_topic(
        release_candidate("## New Features - Background agents can run coding tasks asynchronously. (#10)")
    )

    assert topic["specificity"] == "high"
    assert topic["primary_angle"]["title"].startswith("Codex:")
    assert "Background agents" in topic["primary_angle"]["title"]
    assert topic["primary_angle"]["evidence"]["text"].endswith("(#10)")
    assert topic["alternative_angles"] == []


def test_multiple_meaningful_changes_keep_one_primary_and_two_alternatives() -> None:
    topic = extract_release_topic(
        release_candidate(
            "## New Features "
            "- Vim mode supports search navigation in drafts. (#1) "
            "- Rate-limit banners offer usage and credit actions. (#2) "
            "- MCP tools support output token limits. (#3) "
            "- The CLI adds a fourth useful command. (#4)"
        )
    )

    assert "Vim mode" in topic["primary_angle"]["title"]
    assert len(topic["alternative_angles"]) == 2
    assert "Rate-limit" in topic["alternative_angles"][0]["title"]
    assert "MCP tools" in topic["alternative_angles"][1]["title"]


def test_release_with_mostly_bug_fixes_uses_low_specificity_fallback() -> None:
    topic = extract_release_topic(
        release_candidate(
            "## Bug Fixes - Fixed a Bash task-output error on macOS. "
            "- Fixed settings not saving in some projects. - Resolved occasional session stalls."
        )
    )

    assert topic["specificity"] == "low"
    assert topic["primary_angle"]["evidence"] is None
    assert topic["fallback_reason"] == "no meaningful standalone feature extracted from available release notes"


def test_version_only_release_does_not_invent_a_topic() -> None:
    topic = extract_release_topic(release_candidate(""))

    assert topic["primary_angle"]["title"] == "Codex 1.2.3 Release"
    assert topic["primary_angle"]["query"] == "Codex 1.2.3 release"
    assert topic["specificity"] == "low"


def test_maintenance_bullet_cannot_displace_a_feature() -> None:
    topic = extract_release_topic(
        release_candidate(
            "## Maintenance - Updated packages and bumped dependencies. "
            "## New Features - The CLI adds a workspace automation command."
        )
    )

    assert "workspace automation command" in topic["primary_angle"]["title"]
    assert "dependencies" not in topic["primary_angle"]["title"].lower()


def test_repository_syntax_does_not_leak_into_angle_title() -> None:
    topic = extract_release_topic(
        release_candidate(
            "## New Features - Adds background task notifications for coding agents.",
            repository="example-owner/agent-runner",
        )
    )

    assert "example-owner/agent-runner" not in topic["primary_angle"]["title"]
    assert "/" not in topic["primary_angle"]["title"]


def test_primary_angle_produces_natural_feature_query() -> None:
    topic = extract_release_topic(
        release_candidate("## New Features - Vim mode supports search navigation within command drafts.")
    )
    query = topic["primary_angle"]["query"]

    assert query.startswith("Codex Vim mode")
    assert "openai/codex" not in query
    assert "1.2.3" not in query
    assert "tutorial" not in query.lower()


def test_attaching_topics_keeps_one_release_as_one_candidate() -> None:
    candidate = release_candidate("## New Features - Adds an agent workflow command.")
    candidates = [candidate]

    attach_video_topics(candidates)

    assert len(candidates) == 1
    assert candidates[0] is candidate
    assert candidate.video_topic["parent_candidate_fingerprint"] == candidate.fingerprint


def test_alternative_angles_preserve_exact_evidence() -> None:
    second_bullet = "MCP tools support output token limits across resumed sessions. (#22)"
    topic = extract_release_topic(
        release_candidate(
            "## New Features - The CLI adds workspace automation. (#21) "
            f"- {second_bullet}"
        )
    )

    assert topic["alternative_angles"][0]["evidence"] == {
        "section": "New Features",
        "text": second_bullet,
    }


def test_topic_attachment_does_not_change_discovery_priority() -> None:
    candidate = release_candidate("## New Features - Adds an agent workflow command.")
    original_priority = candidate.discovery_priority

    attach_video_topics([candidate])

    assert candidate.discovery_priority == original_priority


def test_meaningful_feature_release_remains_in_main_recommendations() -> None:
    candidate = release_candidate("## New Features - Adds an agent workflow command.")
    attach_video_topics([candidate])

    main, release_watch = partition_topicable_candidates([candidate], 10)

    assert main == [candidate]
    assert release_watch == []
    assert candidate.video_topic["topicability"]["status"] == "actionable"


def test_maintenance_only_release_moves_to_release_watch() -> None:
    candidate = release_candidate("## Maintenance - Updated packages and bumped dependencies.")
    attach_video_topics([candidate])

    main, release_watch = partition_topicable_candidates([candidate], 10)

    assert main == []
    assert release_watch == [candidate]
    assert candidate.video_topic["topicability"]["reason"] == (
        "fresh release has no defensible standalone video angle"
    )


def test_version_only_release_moves_to_release_watch() -> None:
    candidate = release_candidate("")
    attach_video_topics([candidate])

    main, release_watch = partition_topicable_candidates([candidate], 10)

    assert main == []
    assert release_watch == [candidate]
    assert candidate.video_topic["primary_angle"]["evidence"] is None


def test_low_specificity_release_with_strong_interest_is_promoted() -> None:
    candidate = release_candidate("## Bug Fixes - Fixed an occasional display glitch.")
    candidate.interest_band = "strong"
    candidate.interest_rule = "HN points >= 100"
    attach_video_topics([candidate])

    main, release_watch = partition_topicable_candidates([candidate], 10)

    assert main == [candidate]
    assert release_watch == []
    assert candidate.video_topic["topicability"]["status"] == "promoted"
    assert "HN points >= 100" in candidate.video_topic["topicability"]["promotion_rule"]


def test_release_watch_slot_is_filled_by_next_actionable_candidate() -> None:
    watched = release_candidate("")
    actionable = Candidate(
        fingerprint="actionable-project",
        title="A specific AI developer workflow tool",
        entity=None,
        effective_event_time=NOW - timedelta(hours=2),
        items=[],
        source_families=["hacker_news"],
        discovery_priority=70.0,
    )
    attach_video_topics([watched, actionable])

    main, release_watch = partition_topicable_candidates([watched, actionable], 1)

    assert main == [actionable]
    assert release_watch == [watched]
