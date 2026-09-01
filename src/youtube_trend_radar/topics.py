from __future__ import annotations

from hashlib import sha256
from typing import Any
import re

from youtube_trend_radar.models import Candidate, SourceItem, isoformat
from youtube_trend_radar.utils import clean_text


EXTRACTION_VERSION = "release-topic-v1.1"
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._/-]*")
REPOSITORY_RE = re.compile(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b")
ISSUE_RE = re.compile(r"\s*\(#\d+(?:,\s*#\d+)*\)")

PRODUCT_PATTERNS = [
    ("claude code", "Claude Code"),
    ("gemini cli", "Gemini CLI"),
    ("github copilot", "GitHub Copilot"),
    ("model context protocol", "MCP"),
    ("modelcontextprotocol", "MCP"),
    ("openrouter", "OpenRouter"),
    ("hugging face", "Hugging Face"),
    ("huggingface", "Hugging Face"),
    ("deepseek", "DeepSeek"),
    ("ollama", "Ollama"),
    ("codex", "Codex"),
    ("cursor", "Cursor"),
]
REPOSITORY_PRODUCTS = {
    "openai/codex": "Codex",
    "anthropics/claude-code": "Claude Code",
    "google-gemini/gemini-cli": "Gemini CLI",
    "modelcontextprotocol/servers": "MCP Servers",
    "ollama/ollama": "Ollama",
    "huggingface/huggingface_hub": "Hugging Face Hub",
}
DEFAULT_NOISE_SECTIONS = [
    "bug fix",
    "fixes",
    "chore",
    "maintenance",
    "dependencies",
    "documentation",
    "internal",
    "tests",
]
DEFAULT_NOISE_TERMS = [
    "dependency bump",
    "bump dependency",
    "documentation only",
    "docs only",
    "typo",
    "internal refactor",
    "code cleanup",
    "test coverage",
    "ci configuration",
    "updated packages",
]
DEFAULT_CAPABILITY_TERMS = [
    "add",
    "introduc",
    "support",
    "enable",
    "allow",
    "offer",
    "can now",
    "new command",
    "new capability",
    "available",
    "preview",
    "beta",
    "integration",
    "configur",
]
DEFAULT_DEVELOPER_TERMS = [
    "agent",
    "api",
    "authentication",
    "cli",
    "code",
    "command",
    "config",
    "editor",
    "ide",
    "mcp",
    "model",
    "sdk",
    "server",
    "shell",
    "terminal",
    "tool",
    "workflow",
]
DEFAULT_HIGH_IMPACT_TERMS = [
    "breaking",
    "deprecat",
    "security",
    "vulnerability",
    "availability",
    "performance",
    "latency",
    "faster",
]
FEATURE_SECTIONS = ["new feature", "features", "enhancement", "new capabilities", "added"]
QUERY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "can",
    "for", "from", "has", "have", "in", "including", "into", "is", "it", "its",
    "new", "now", "of", "on", "or", "that", "the", "their", "through", "to",
    "was", "were", "when", "with", "within", "fixed", "fixes", "added", "adds",
    "support", "supports", "updated", "update", "changed", "changes",
}


def release_item(candidate: Candidate) -> SourceItem | None:
    return next((item for item in candidate.items if item.item_type == "github_release"), None)


def repository_name(candidate: Candidate) -> str | None:
    return next(
        (str(item.metrics["repo_full_name"]).lower() for item in candidate.items if item.metrics.get("repo_full_name")),
        None,
    )


def humanize_slug(value: str) -> str:
    words = re.sub(r"[-_]+", " ", value).split()
    return " ".join(
        word.upper() if word.lower() in {"ai", "api", "cli", "mcp", "sdk"} else word.capitalize()
        for word in words
    )


def product_name(candidate: Candidate) -> str:
    repository = repository_name(candidate)
    if repository in REPOSITORY_PRODUCTS:
        return REPOSITORY_PRODUCTS[repository]
    title_lower = candidate.title.lower()
    for pattern, product in PRODUCT_PATTERNS:
        if pattern in title_lower:
            return product
    if repository:
        return humanize_slug(repository.split("/", 1)[-1])
    title = re.sub(r"^(?:Show|Launch) HN:\s*", "", candidate.title, flags=re.I)
    return clean_text(re.split(r"\s+[–—:-]\s+", title, maxsplit=1)[0], limit=50) or candidate.entity or "AI developer tool"


def release_version(candidate: Candidate) -> str | None:
    item = release_item(candidate)
    if not item:
        return None
    tag = str(item.metrics.get("release_tag") or "").strip()
    return re.sub(r"^(?:rust-)?v(?=\d)", "", tag, flags=re.I) or None


def without_repository_syntax(text: str) -> str:
    def human_name(match: re.Match[str]) -> str:
        return humanize_slug(match.group(0).split("/", 1)[1])

    return clean_text(REPOSITORY_RE.sub(human_name, text).replace("/", " "), limit=120)


def query_text(product: str, *parts: str | None) -> str:
    text = without_repository_syntax(" ".join([product, *(part for part in parts if part)]))
    words: list[str] = []
    seen: set[str] = set()
    for word in text.split():
        word = word.strip("()[]{}:;,")
        lower = word.lower()
        if word and lower not in seen:
            seen.add(lower)
            words.append(word)
    return clean_text(" ".join(words), limit=100)


def query_phrase(text: str) -> str | None:
    cleaned = ISSUE_RE.sub("", text.strip().lstrip("-* "))
    cleaned = re.sub(r"[`*_#]", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    tokens: list[str] = []
    seen: set[str] = set()
    for token in WORD_RE.findall(cleaned):
        value = token.strip("./").replace("_", " ")
        lower = value.lower()
        if not value or lower in QUERY_STOPWORDS or lower in seen or lower.isdigit():
            continue
        seen.add(lower)
        tokens.append(value)
        if len(tokens) == 8:
            break
    return " ".join(tokens) if len(tokens) >= 3 else None


def _configured_terms(config: dict[str, Any], key: str, default: list[str]) -> list[str]:
    values = config.get(key, default)
    return [str(value).lower() for value in values] if isinstance(values, list) else list(default)


def _release_bullets(summary: str) -> list[dict[str, Any]]:
    normalized = re.sub(r"\s+(?=#{1,6}\s+)", "\n", summary)
    normalized = re.sub(r"\s+(?=[-*]\s+)", "\n", normalized)
    section = ""
    bullets: list[dict[str, Any]] = []
    for part in normalized.splitlines():
        value = part.strip()
        heading = re.match(r"^#{1,6}\s+(.+)$", value)
        if heading:
            section = clean_text(heading.group(1), limit=100)
            continue
        if re.match(r"^[-*]\s+", value):
            text = clean_text(re.sub(r"^[-*]\s+", "", value), limit=1000)
            if text:
                bullets.append({"section": section or None, "text": text, "position": len(bullets)})
    if not bullets and summary and not re.search(r"#{1,6}\s+", summary):
        bullets.append({"section": None, "text": clean_text(summary, limit=1000), "position": 0})
    return bullets


def _meaningful_change(
    bullet: dict[str, Any],
    *,
    noise_sections: list[str],
    noise_terms: list[str],
    capability_terms: list[str],
    developer_terms: list[str],
    high_impact_terms: list[str],
) -> tuple[int, str] | None:
    section = str(bullet.get("section") or "").lower()
    text = str(bullet["text"]).lower()
    high_impact = any(term in text or term in section for term in high_impact_terms)
    noisy_section = any(term in section for term in noise_sections)
    noisy_text = any(term in text for term in noise_terms)
    maintenance_verb = bool(re.match(r"^(?:fixed|fixes|resolved|bumped|updated)\b", text))
    if (noisy_section or noisy_text or maintenance_verb) and not high_impact:
        return None

    capability = any(term in text for term in capability_terms)
    developer_relevant = any(term in text for term in developer_terms)
    feature_section = any(term in section for term in FEATURE_SECTIONS)
    material_performance = high_impact and bool(re.search(r"\d+(?:\.\d+)?(?:%|x|\s?ms|\s?s)\b", text))
    if feature_section and (capability or developer_relevant or len(WORD_RE.findall(text)) >= 4):
        return 0, "feature-section change with developer or capability evidence"
    if high_impact and (developer_relevant or material_performance):
        return 1, "developer-facing breaking, security, availability, or material performance change"
    if capability and developer_relevant:
        return 2, "developer-facing capability language"
    return None


def _evidence_text(text: str) -> str:
    value = ISSUE_RE.sub("", text)
    value = re.sub(r"[`*_#]", "", value)
    return clean_text(value).rstrip(".")


def _angle_title(product: str, evidence: str) -> str:
    text = _evidence_text(evidence)
    replacements = {
        "added ": "Adds ",
        "introduced ": "Introduces ",
        "enabled ": "Enables ",
        "improved ": "Improves ",
        "deprecated ": "Deprecates ",
        "removed ": "Removes ",
    }
    lower = text.lower()
    for prefix, replacement in replacements.items():
        if lower.startswith(prefix):
            return clean_text(f"{product} {replacement}{text[len(prefix):]}", limit=120).rstrip(".,;:")
    if lower.startswith("new "):
        return clean_text(f"{product} Adds {text[4:]}", limit=120).rstrip(".,;:")
    return clean_text(f"{product}: {text[:1].upper()}{text[1:]}", limit=120).rstrip(".,;:")


def _angle(product: str, release: SourceItem, bullet: dict[str, Any], rule: str) -> dict[str, Any]:
    phrase = query_phrase(str(bullet["text"]))
    evidence = {"section": bullet.get("section"), "text": bullet["text"]}
    identity = sha256(f"{release.external_id}|{bullet['position']}|{bullet['text']}".encode()).hexdigest()[:12]
    return {
        "angle_id": identity,
        "title": _angle_title(product, str(bullet["text"])),
        "query": query_text(product, phrase or _evidence_text(str(bullet["text"]))),
        "evidence": evidence,
        "selection_rule": rule,
    }


def extract_release_topic(candidate: Candidate, config: dict[str, Any] | None = None) -> dict[str, Any]:
    release = release_item(candidate)
    if not release:
        return {}
    config = config or {}
    product = product_name(candidate)
    version = release_version(candidate)
    meaningful: list[tuple[int, int, dict[str, Any], str]] = []
    options = {
        "noise_sections": _configured_terms(config, "noise_sections", DEFAULT_NOISE_SECTIONS),
        "noise_terms": _configured_terms(config, "noise_terms", DEFAULT_NOISE_TERMS),
        "capability_terms": _configured_terms(config, "capability_terms", DEFAULT_CAPABILITY_TERMS),
        "developer_terms": _configured_terms(config, "developer_terms", DEFAULT_DEVELOPER_TERMS),
        "high_impact_terms": _configured_terms(config, "high_impact_terms", DEFAULT_HIGH_IMPACT_TERMS),
    }
    for bullet in _release_bullets(release.summary):
        classification = _meaningful_change(bullet, **options)
        if classification:
            tier, rule = classification
            meaningful.append((tier, int(bullet["position"]), bullet, rule))
    meaningful.sort(key=lambda value: (value[0], value[1]))

    fallback_reason = None
    if meaningful:
        angles = [_angle(product, release, bullet, rule) for _, _, bullet, rule in meaningful[:3]]
        specificity = "high" if meaningful[0][0] == 0 else "medium"
    else:
        fallback_reason = "no meaningful standalone feature extracted from available release notes"
        label = clean_text(" ".join(value for value in (product, version, "Release") if value), limit=120)
        angles = [
            {
                "angle_id": sha256(f"{release.external_id}|fallback".encode()).hexdigest()[:12],
                "title": label,
                "query": query_text(product, version, "release"),
                "evidence": None,
                "selection_rule": "version-only fallback",
            }
        ]
        specificity = "low"

    return {
        "kind": "release",
        "parent_event_id": release.external_id,
        "parent_candidate_fingerprint": candidate.fingerprint,
        "release_title": candidate.title,
        "release_url": release.canonical_url,
        "release_version": version,
        "release_published_at": isoformat(release.published_at),
        "primary_angle": angles[0],
        "alternative_angles": angles[1:3],
        "specificity": specificity,
        "extraction_version": EXTRACTION_VERSION,
        "fallback_reason": fallback_reason,
    }


def attach_video_topics(candidates: list[Candidate], config: dict[str, Any] | None = None) -> None:
    for candidate in candidates:
        candidate.video_topic = extract_release_topic(candidate, config)
