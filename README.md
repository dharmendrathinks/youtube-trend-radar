# YouTube Trend Radar

[![CI](https://github.com/dharmendrathinks/youtube-trend-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/dharmendrathinks/youtube-trend-radar/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

A developer-focused, event-first radar for finding fresh AI/developer video topics before they become obvious everywhere.

`youtube-trend-radar` watches upstream ecosystem events, ranks the ones worth investigating, and attaches recent YouTube evidence for manual coverage review. It is deterministic, runs without an LLM, and does **not** claim to predict virality.

```text
Official releases/changelogs + GitHub watchlist/exploration
                         + Hacker News + Hugging Face
                                      ↓
       normalization + freshness + evidence + observed interest
                                      ↓
                             Top Opportunities
                                      ↓
                  YouTube evidence for manual inspection
```

YouTube evidence never affects Discovery Priority. V1 does not calculate a default YouTube crowding or opportunity score.

## Output preview

```text
Top Opportunities — 6 found

1. Codex: Vim-mode search/navigation
   Priority: 89.2 · Interest: strong

2. Claude Code Opus 5 Auto Mode
   Priority: 68.5 · Interest: strong

Release Watch: 5
Community Watch: 2
```

[See how scoring works ↓](#discovery-priority)

## Why this exists

Most trend tools become useful after attention has already accumulated. For a creator covering coding agents, AI IDEs, MCP, local AI, developer models, SDKs, and open-source tools, that can be too late.

This project starts farther upstream. A new official release can matter before it trends; early GitHub, Hacker News, or Hugging Face activity can strengthen the case; YouTube then helps the operator inspect whether the exact viewer intent is already being served.

## Quick start

Requirements: Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/dharmendrathinks/youtube-trend-radar.git
cd youtube-trend-radar

cp config.example.toml config.toml
cp .env.example .env

uv sync
uv run youtube-trend-radar doctor
uv run youtube-trend-radar scan
```

Useful variants:

```bash
# Run discovery without YouTube API requests.
uv run youtube-trend-radar scan --no-youtube

# Request at most five Top Opportunities. The floor may return fewer.
uv run youtube-trend-radar scan --top 5

# Use a configuration outside the repository root.
uv run youtube-trend-radar scan --config path/to/config.toml
```

Every scan writes uniquely named Markdown and JSON reports under `reports/`, plus local `latest.md` and `latest.json` pointers. SQLite observations and HTTP cache records live under `data/`. These paths, `.env`, and `config.toml` are intentionally ignored by Git.

## What it watches

| Source | V1 responsibility | Credential |
|---|---|---|
| Official RSS/Atom feeds | Product releases, changelogs, and authoritative announcements | None |
| GitHub watched repositories | Releases plus repeated aggregate repository observations | `GITHUB_TOKEN` optional, recommended |
| GitHub exploration | Newly created AI/developer repositories outside the watchlist | `GITHUB_TOKEN` optional, recommended |
| Hacker News | Relevant submissions, points, comments, and observed change | None |
| Hugging Face | Emerging models and Spaces with supported public metadata | `HF_TOKEN` optional |
| YouTube | Recent video metadata and direct searches for manual coverage inspection | `YOUTUBE_API_KEY` optional |

Providers are isolated: one unavailable provider does not terminate an otherwise usable scan. Reports identify failures, stale/cache state, and missing evidence.

## Output model

- **Top Opportunities** — actionable topics worth investigating now. The configured count is a maximum; the radar returns fewer results rather than backfilling weak candidates.
- **Release Watch** — release or authoritative changelog events that lack a sufficiently useful/current video angle or do not meet the main-list presentation floor.
- **Community Watch** — relevant discoveries retained outside the primary list because of weak or stagnant evidence, English-orientation gates, freshness, or insufficient promotion evidence.

Each recommendation includes timestamps, score inputs, triggering rules, observed signals, missing evidence, source links, and YouTube evidence when available. Markdown is designed for reading; JSON is suitable for downstream tooling.

## Discovery Priority

Freshness uses the best credible event timestamp and a configurable 48-hour half-life:

```text
Freshness = 100 × 2 ^ (-age_hours / 48)
```

Evidence Strength reflects observable provenance: an authoritative source, independent confirmation, or a single community source. Interest is a configured `strong`, `moderate`, or `early/limited` band backed by current HN, GitHub, Hugging Face, and source-family measurements.

```text
Discovery Priority = 0.60 × Freshness
                   + 0.25 × Evidence Strength
                   + 0.15 × Interest Value
```

Discovery Priority orders discovery evidence; it is not a probability, virality forecast, or YouTube opportunity score. A separate presentation floor requires sufficient freshness plus moderate/strong interest, independent confirmation, or authoritative actionable evidence before a candidate enters Top Opportunities. All thresholds live in `config.toml` and are starting heuristics, not scientifically calibrated predictions.

## Credentials

Copy `.env.example` to `.env` and set only the credentials you want to use:

```dotenv
GITHUB_TOKEN=
HF_TOKEN=
YOUTUBE_API_KEY=
```

- **`GITHUB_TOKEN`** — optional but strongly recommended. Without it, GitHub uses anonymous public API access with substantially lower rate limits; GitHub providers degrade independently if that quota is exhausted.
- **`HF_TOKEN`** — optional for public models and Spaces. It can improve authenticated access but is not required for normal public discovery.
- **`YOUTUBE_API_KEY`** — optional. Without it, discovery and ranking still work and reports provide manual YouTube search links, but no live YouTube video metadata is retrieved.

Never commit `.env`, credentials, private reports, or local databases. The CLI suppresses verbose HTTP logging that could otherwise expose query-string credentials, and cached URLs redact sensitive parameters.

## First run and repeated runs

The radar never invents historical momentum.

On a repository or story's first observation, the report shows current aggregates and explicitly marks observed growth as unavailable. Repeated scans allow SQLite to measure changes since tracking began, including:

- GitHub stars at first observation, current stars, observed delta, and duration.
- Hacker News point/comment change over the observation window.
- Hugging Face metric changes where supported.

These are **observed changes while your radar was running**, not reconstructed historical growth.

## YouTube evidence and limitations

For each promoted candidate, V1 generates up to two compact event-specific searches and retrieves supported recent video metadata. Search requests use `type=video`, relevance ordering, an English relevance-language preference, and a configurable publication window.

YouTube search can still return noisy or loosely related videos. V1 preserves YouTube-returned content and order for manual inspection and deliberately avoids an expanding list of negative keywords. It does not silently filter results or calculate a default relevance ratio, crowding score, views-per-hour metric, creator tier, or YouTube-derived Opportunity Score.

Optional deterministic title/channel annotations exist behind `youtube.enable_local_relevance_annotations`, disabled by default. Enable them only after reviewing and complying with YouTube's applicable derived-metrics terms. See the [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies), [`search.list` documentation](https://developers.google.com/youtube/v3/docs/search/list), and [derived metrics policy](https://developers.google.com/youtube/terms/derived-metrics-policy).

## Configuration

`config.example.toml` is a runnable, credential-free starting point. Copy it to `config.toml` before running the CLI. It controls:

- Official feeds, watched repositories, and GitHub exploration queries.
- Provider result bounds, lookback windows, caching, and retries.
- Entity aliases and developer-channel relevance terms.
- Deduplication anchors and release-topic extraction terms.
- Eligibility, interest, English-orientation, stagnation, and main-list thresholds.
- Ranking weights and YouTube request budgets.

Thresholds are deliberately external to code so real scan results can inform later tuning. Reports persist the effective values and configuration fingerprint.

## Architecture

The project is one Python package and one CLI:

```text
provider modules (concurrent, failure-isolated)
    → normalized SourceItem records
    → conservative entity resolution and event clustering
    → deterministic scoring and presentation gates
    → optional YouTube evidence
    → Markdown + JSON reports

SQLite
    ↳ source observations
    ↳ aggregate-change history
    ↳ HTTP cache
    ↳ reproducible scan records
```

There are no microservices, queues, cloud dependencies, vector databases, scraping services, or mandatory AI APIs.

## Development

Install the project and credential-free test dependencies:

```bash
uv sync --extra dev
```

Run the checks used for release preparation:

```bash
uv run pytest
uv run youtube-trend-radar --help
uv run youtube-trend-radar doctor
uv build
```

Tests use fixtures and mocked HTTP responses; they do not require GitHub, Hugging Face, or YouTube credentials. `doctor` is a live connectivity check and may warn about optional missing credentials.

See [CONTRIBUTING.md](CONTRIBUTING.md) before changing provider behavior, eligibility, or scoring.

## V1 limitations

- Deterministic heuristics require calibration against real use; they are not learned predictions.
- No runtime LLM, semantic embedding model, or virality prediction is used.
- Growth is measured only after local tracking begins.
- Provider availability, API quotas, upstream schemas, and feed quality constrain results.
- Presentation is English-oriented using a transparent Latin-script proxy, not full language identification.
- Entity resolution and deduplication are intentionally conservative, so occasional duplicates are preferred over incorrect merges.
- YouTube competition remains a manual judgment.
- No dashboard, alerts, historical backfill, Reddit, X/Twitter, Google Trends, or paid trend provider is included.

## Source APIs and attribution

- [GitHub REST API](https://docs.github.com/en/rest)
- [Hacker News API](https://github.com/HackerNews/API)
- [Hugging Face Hub API](https://huggingface.co/docs/huggingface_hub/package_reference/hf_api)
- [YouTube Data API](https://developers.google.com/youtube/v3)

Individual source names and links are retained in generated reports. Use of every API remains subject to its terms, quotas, attribution requirements, and future policy changes.

## Project policies

- Contributions: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security reports: [SECURITY.md](SECURITY.md)
- License: [MIT](LICENSE)
