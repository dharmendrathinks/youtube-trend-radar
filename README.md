# YouTube Trend Radar

Find fresh AI and developer-tool topics before YouTube catches up.

`youtube-trend-radar` collects live ecosystem events, ranks them with deterministic discovery heuristics, and shows recent YouTube videos for manual competition review. It is an event radar, not a virality predictor.

## What V1 answers

> Here are the freshest promising AI/developer topics, plus relevant YouTube evidence for you to inspect.

The ranked value is **Discovery Priority**. YouTube does not affect that value in V1, so the project does not claim an automatic opportunity or crowding score.

## Sources

- Configured official RSS/Atom feeds.
- Watched GitHub repositories and releases through the official REST API.
- Exploratory GitHub repository search.
- Hacker News through the official Firebase API.
- Hugging Face models and Spaces through the supported Hub client.
- YouTube validation through the YouTube Data API.

A provider can fail without terminating the scan. Reports identify failed, cached, stale, and disabled evidence.

## Requirements

- Python 3.12 or newer.
- A GitHub token is optional but strongly recommended.
- A Hugging Face token is optional for public data.
- A YouTube Data API key is required only for live YouTube video evidence.

No paid data source or LLM is required.

## Setup

Using [`uv`](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/dharmendrathinks/youtube-trend-radar.git
cd youtube-trend-radar
cp config.example.toml config.toml
cp .env.example .env
uv sync --extra dev --python 3.12
```

Or with standard Python:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp config.example.toml config.toml
cp .env.example .env
```

Edit `.env` as needed:

```dotenv
GITHUB_TOKEN=
HF_TOKEN=
YOUTUBE_API_KEY=
```

Neither `.env` nor `config.toml` is tracked. Never place credentials in example files.

## Usage

Check installation and live connectivity:

```bash
uv run youtube-trend-radar doctor
```

Run a complete scan:

```bash
uv run youtube-trend-radar scan
```

Useful options:

```bash
uv run youtube-trend-radar scan --top 5
uv run youtube-trend-radar scan --no-youtube
uv run youtube-trend-radar scan --config path/to/config.toml
```

Each run creates uniquely timestamped Markdown and JSON files under `reports/`, plus `latest.md` and `latest.json`. SQLite observations and HTTP cache data live under `data/`. Both directories are ignored by Git, so the first live scan is preserved locally without entering the repository.

## Configuration

`config.example.toml` is a runnable starting point. It controls:

- Official feeds and watched repositories.
- GitHub exploration queries and result bounds.
- Whether prerelease/nightly GitHub releases should be included (disabled by default).
- Entity aliases, categories, and relevance terms.
- Scan windows and provider limits.
- HTTP caching and retries.
- All candidate-eligibility and interest-band thresholds.
- Ranking weights and YouTube request budget.

Eligibility and interest values such as HN points, observed GitHub star changes, and Hugging Face likes are uncalibrated defaults. Tune `[ranking.eligibility]` and `[ranking.interest]` after inspecting real scans. Their effective values and the configuration fingerprint are saved with each result.

GitHub star changes are always **observed growth since tracking began**. The radar never invents historical star velocity.

Watched repository snapshots are supporting observations, not fresh events. An old repository can become a candidate only through a release or a configured observed-growth trigger that requires minimum history plus absolute and relative growth. Newly created repositories use their actual creation time.

## Ranking

Candidates must match a developer-focused product anchor, contain both AI/model and developer/tool/workflow signals, or describe a configured model-release event. News and official announcements are judged by their primary title rather than incidental product mentions in summaries; project-oriented Show HN, Launch HN, GitHub, and Hugging Face items may use their descriptions to establish what the project does. General company, legal, policy, and cultural news does not pass solely because it mentions OpenAI, Anthropic, Google, or Hugging Face. Entity assignment uses explicit provider identity or title/URL identity; incidental summary mentions do not assign the event's primary entity.

Single-source community candidates also cross a configurable evidence gate before ranking. For Hacker News, the default is at least five points or two comments. Weak items remain stored for later observations and can become eligible after gaining evidence or receiving independent confirmation.

Freshness has a configurable 48-hour half-life:

```text
Freshness = 100 × 2 ^ (-age_hours / 48)
```

Discovery order is:

```text
Discovery Priority = 0.60 × Freshness
                   + 0.25 × Evidence Strength
                   + 0.15 × Interest Value
```

Evidence Strength comes from source provenance and independent source families. Interest is a configured `strong`, `moderate`, or `early/limited` band. Reports expose every score input and triggering rule.

Deduplication is deliberately conservative. Exact canonical targets and release identities merge automatically. Similar titles only support a merge when a shared version, model, repository, or configured feature anchor also matches. Duplicate recommendations are preferred over merging separate releases.

## YouTube evidence

For each top candidate, V1 generates up to two event-specific searches and retrieves supported recent video metadata. It shows exact queries, manual search links, titles, channels, dates, durations, and available public statistics.

YouTube evidence is never included in Discovery Priority. V1 does not calculate crowding, creator quality, views per hour, Shorts classification, or whether an intent is fully served. If the API key is absent, ranking still works and the report provides manual search links.

YouTube search requests are quota-expensive. The default configuration caps them at 20 per scan and caches results for six hours.

## Tests

```bash
uv run pytest
```

The default suite uses fixtures and mocked HTTP responses; it requires no network or secrets. Live validation is performed explicitly with `doctor` and `scan`.

## Source attribution and API policies

- [GitHub REST API documentation](https://docs.github.com/en/rest)
- [Hacker News API documentation](https://github.com/HackerNews/API)
- [Hugging Face Hub API documentation](https://huggingface.co/docs/huggingface_hub/package_reference/hf_api)
- [YouTube Data API documentation](https://developers.google.com/youtube/v3)
- Individual feed names and source links are retained in every report.

Use of each API remains subject to its terms, quotas, attribution requirements, and policy changes. The project uses supported APIs and feeds and does not scrape YouTube or generic changelog pages.

## Known V1 limitations

- Ranking thresholds are useful starting heuristics, not calibrated predictions.
- YouTube competition requires manual interpretation.
- Official sources without stable feeds or APIs are absent unless represented through official GitHub releases.
- Hugging Face trending position is treated as opaque supporting metadata.
- First scans cannot report observed growth; later scans can.
- Entity resolution and event clustering are deterministic and intentionally conservative.
- No dashboard, notifications, LLM clustering, historical backfill, or generic web scraping is included.

See [PLAN.md](PLAN.md) for the approved V1 product and architecture decisions.

## License

MIT
