# YouTube Trend Radar — Smallest Robust End-to-End V1

## Summary

`youtube-trend-radar` will be a small Python CLI that discovers fresh AI/developer events from live sources, ranks them with transparent deterministic heuristics, and attaches precise YouTube search evidence so a creator can decide what to investigate.

V1 is optimized for one complete implementation pass and useful day-one results. It will use official feeds/APIs, SQLite, Markdown and JSON reports, repeated aggregate snapshots, and isolated provider failures. It will not require an LLM, historical calibration, a dashboard, or a custom YouTube crowding metric.

The V1 product promise is deliberately narrow:

> Here are the freshest promising AI/developer topics, plus relevant YouTube evidence for you to inspect.

V1 does not claim to calculate an automatic YouTube opportunity score. To keep that boundary clear, the ranked value is called **Discovery Priority**, not Opportunity Score. YouTube evidence is excluded from Discovery Priority and remains an explicit manual judgment input.

## 1. Exact V1 Scope

The primary command will be:

```text
youtube-trend-radar scan
```

A scan will:

1. Load configuration and credentials.
2. Collect live items from official sources, GitHub, Hacker News, and Hugging Face.
3. Normalize all items into one small source-item model.
4. Discard items that are not relevant to AI/developer content.
5. Merge only obvious duplicates describing the same event.
6. Calculate freshness and observable evidence strength.
7. Rank candidates using non-YouTube discovery evidence.
8. For selected releases, extract one grounded primary video angle and up to two alternatives without creating new candidates.
9. Generate precise viewer-intent YouTube searches for the highest-ranked candidates.
10. Retrieve supported YouTube metadata and recent competing videos.
11. Persist the scan and observations in SQLite.
12. Write readable Markdown and machine-readable JSON reports.

Defaults:

- Seven-day discovery lookback.
- Ten recommendations per report.
- YouTube validation for the top ten candidates.
- Global, English-oriented discovery.
- Deterministic ranking with no mandatory LLM.
- YouTube evidence shown alongside, but excluded from, ranking.

V1 includes all six provider responsibilities:

1. Official AI/developer release sources.
2. Watched GitHub repositories and releases.
3. Exploratory GitHub discovery for unknown projects.
4. Hacker News.
5. Hugging Face models and Spaces.
6. YouTube validation and evidence.

## 2. Simplest Robust Architecture

Use a single-process Python 3.12 application with one CLI and no services.

```text
CLI/config
    ↓
concurrent provider collection
    ↓
normalization and relevance filtering
    ↓
conservative deduplication
    ↓
freshness/evidence ranking
    ↓
deterministic release-to-video-angle extraction
    ↓
YouTube validation for top candidates
    ↓
SQLite persistence + Markdown/JSON reports
```

Implementation choices:

- Standard-library `argparse` for the CLI.
- Standard-library `sqlite3` for persistence.
- Standard-library `tomllib` for configuration.
- Environment variables for credentials.
- `ThreadPoolExecutor` for independent network providers.
- Main-thread normalization, database writes, ranking, and report generation.
- Small provider modules behind a common result shape, not a general plugin system.

Minimal runtime dependencies:

- `httpx` for HTTP.
- `feedparser` for RSS/Atom.
- `huggingface_hub` for supported Hugging Face access.
- `python-dotenv` for local environment loading.

Development dependencies:

- `pytest`.
- `respx` for deterministic HTTP tests.

CLI surface:

```text
youtube-trend-radar scan
youtube-trend-radar scan --config PATH --top N
youtube-trend-radar scan --no-youtube
youtube-trend-radar doctor
```

`doctor` validates configuration, database access, credentials, and provider connectivity without running a full scan.

One TOML configuration file contains:

- Scan lookback and result count.
- Official RSS/Atom feeds.
- Watched GitHub repositories.
- GitHub exploration query packs.
- Entity aliases and relevance keywords.
- Hugging Face filters.
- Interest-band thresholds.
- YouTube query/result budget.
- HTTP timeout and cache settings.

Credentials:

- `GITHUB_TOKEN`: optional but strongly recommended.
- `HF_TOKEN`: optional.
- `YOUTUBE_API_KEY`: required only for live YouTube evidence.

Cron or launchd usage may be documented, but V1 does not implement an internal scheduler.

## 3. Provider Responsibilities

Every provider returns a `ProviderResult` containing:

- Provider name.
- Status: `ok`, `partial`, `cached`, `stale`, `failed`, or `disabled`.
- Normalizable items.
- Fetch timestamp.
- Stale-as-of timestamp when applicable.
- Sanitized error summary.
- Request count.

### Official sources

Purpose: detect authoritative announcements before they accumulate social activity.

- Consume configured RSS/Atom feeds through one generic feed collector.
- Ship a small, verified starter configuration for relevant official sources.
- Treat configured official feeds as high-authority evidence.
- Use official GitHub releases when a project's supported release channel is GitHub.
- Do not build a generic HTML changelog scraper.
- Store title, summary, canonical link, published/updated time, feed identity, fetched time, and cache validators.

If an important source has no stable feed or API, document the gap. A source-specific adapter is acceptable only when its endpoint is stable and the adapter is trivial.

### GitHub watched repositories and releases

Purpose: detect releases and meaningful activity in known projects.

- Use the official GitHub REST API.
- Fetch watched repository metadata and recent releases.
- Suggested starter repositories include Codex, Claude Code, Gemini CLI, MCP servers, Ollama, and the Hugging Face client ecosystem.
- Persist aggregate repository observations on every scan.
- Store repository identity, description, topics, timestamps, release metadata, stars, forks, issues, fetched time, and API URL.
- Treat static repository metadata as supporting evidence, never as a newly observed event.

Star growth must be reported only as observed growth:

- Total stars.
- First-observed time.
- Stars at first observation.
- Current stars.
- Observed delta.
- Observation duration.

V1 must not claim historical star velocity for periods before the repository was tracked.

An old watched repository creates a candidate only for a real release or a configured observed-growth trigger. A growth trigger requires minimum observation duration plus both absolute and relative star growth. A newly created watched repository uses its actual creation timestamp; `observed_at` never makes an old repository fresh.

### GitHub exploratory discovery

Purpose: discover emerging projects not already on the watchlist.

- Run a small configured set of recent, non-archived, non-fork repository searches.
- Combine AI/developer categories with recent creation or update windows.
- Bound query and result counts to protect rate limits.
- Normalize results through the same repository path as watched projects.
- Prefer repository age, current aggregate activity, relevance, and later observed deltas over lifetime stars alone.

Queries are configurable because GitHub search syntax and useful ecosystem terms will evolve.

### Hacker News

Purpose: provide fast public evidence of developer discussion.

- Use the official Hacker News Firebase API.
- Inspect bounded slices of new, Show HN, and top story IDs.
- Fetch story title, URL, submission time, score, descendants/comments, and author.
- Persist score and comment observations so later scans can show observed change.
- Do not fetch full comment trees or user profiles.
- Do not depend on Algolia as the primary HN source.

### Hugging Face

Purpose: discover emerging open models, Spaces, and free/open developer tools.

- Use supported Hugging Face Hub client/API operations.
- Collect recent or trending models and Spaces with bounded result sets.
- Store repository ID, kind, URL, tags/pipeline metadata, created/modified time when available, likes, downloads when available, and exposed trending metadata.
- Persist observable likes/downloads for later deltas.
- Treat undocumented or opaque trending positions as supporting evidence, not a precise growth metric.

### YouTube

Purpose: validate exact viewer intent and surface competing coverage.

- Use the supported YouTube Data API.
- Search only the highest-ranked candidates to conserve quota.
- Hydrate video details through supported metadata endpoints.
- Store query, video ID, title, channel, publication time, URL, duration, and supported public statistics where available.
- Cache results and record request usage.
- Do not scrape YouTube pages, fetch transcripts/comments, or infer unavailable metrics.

## 4. Minimal Data Model

### Normalized source item

Each collected item uses a common shape:

- `provider`
- `external_id`
- `source_family`
- `item_type`
- `title`
- `summary`
- `canonical_url`
- `published_at`
- `updated_at`
- `observed_at`
- `entity`
- `categories`
- `authority`
- `metrics`
- `related_links`

Provider-specific metrics remain in a JSON object. V1 does not create a large universal schema for every possible signal.

### Candidate

Candidates are assembled in memory for each scan:

- Stable event fingerprint.
- Specific display title.
- Primary entity.
- Effective event time.
- Supporting source items.
- Independent source families.
- Freshness value.
- Evidence level.
- Interest band.
- Discovery Priority value.
- YouTube queries and videos.
- Missing or uncertain evidence.

V1 will not persist a separate long-lived topic graph or manually curated event lineage.

### SQLite

Use four tables:

1. `source_items` — normalized items and provider payload metadata.
2. `observations` — timestamped aggregates such as stars, HN score/comments, and Hugging Face likes/downloads.
3. `http_cache` — response body, validators, fetch time, and expiry.
4. `scans` — scan metadata, provider statuses, configuration fingerprint, scoring version, and final report JSON.

The JSON report contains:

- Schema and scoring versions.
- Scan ID and generated time.
- Provider statuses.
- Ranked recommendations.
- Event/publication/observation times and age.
- Freshness and Discovery Priority values.
- Evidence level, interest band, and raw supporting signals.
- YouTube status, queries, and videos.
- Explanation, missing evidence, and source links.

## 5. Ranking Approach

Ranking is a transparent prioritization heuristic, not a prediction of virality.

### Relevance gate

A candidate must satisfy at least one channel-relevance rule:

- It matches a watched entity plus a developer-focused product anchor; or
- It contains both an AI/model signal and a developer/tool/workflow signal; or
- It matches a watched model product plus a release/availability event anchor.

An organization name alone is not sufficient: generic company, legal, policy, or cultural news should not pass merely because it mentions OpenAI, Anthropic, Google, or Hugging Face. News and official announcements use the title as their primary relevance surface; incidental summary mentions cannot turn a broad story into a developer-tool event. Project-oriented Show HN, Launch HN, GitHub, and Hugging Face items may use descriptions because those descriptions define the project. Entity assignment uses explicit provider identity or title/URL identity and does not use incidental summary mentions. Configurable product anchors, event terms, include terms, and exclude terms handle these false positives. Items that fail the gate are stored for later observation but not recommended.

### Candidate eligibility

Relevance does not guarantee ranking eligibility. Authoritative events and independently confirmed cross-source events qualify directly. A single-source community candidate must cross a configurable provider-specific evidence floor before Freshness can affect its rank.

Initial configurable floors are:

- Hacker News: at least five points or two comments.
- Exploratory GitHub: at least ten stars.
- Hugging Face: at least ten likes or a reported top-100 trending position.
- Watched-repository growth: at least 24 hours of observation, 50 observed stars, and 0.5% relative growth.

These are starting eligibility heuristics, not calibrated truths. Weak items remain persisted and can qualify on a later scan after accumulating evidence or receiving independent confirmation.

After scoring, two presentation gates can move an otherwise eligible community candidate to **Community Watch** without modifying Freshness, Evidence, Interest, or Discovery Priority:

- English orientation: when every sufficiently long community source is below the configured Latin-script letter ratio, and no official or substantial Latin-script supporting source exists. This is deliberately a simple script heuristic, not language identification.
- Stagnant HN evidence: when a community-only HN item remains below the configured moderate points/comments thresholds and shows no observed point or comment growth after a configurable minimum observation window. Missing first-scan history never triggers this gate.

The default configuration uses at least 20 letters, a `0.60` Latin-script ratio, and a three-hour HN observation window. Effective thresholds, measurements, and the exclusion reason are stored in JSON and shown in Markdown.

A final main-list presentation floor makes the configured result count a maximum, not a target. A recommendation must meet the configured minimum Freshness and at least one observable promotion condition: moderate/strong Interest, independent cross-source confirmation, or authoritative actionable evidence. The shipped Freshness floor is `40`. Candidates below it remain persisted with their unchanged scores, measurements, threshold, and reason in the appropriate watch section. The floor is never relaxed to fill the requested count.

### Conservative deduplication

Automatic merging requires strong event identity. Merge items only when one of these is true:

- Canonical URLs match after normalization.
- They reference the same GitHub repository and release tag.
- A Hacker News submission points to the same normalized canonical target.
- They share the same entity and an explicit event anchor such as a release version, model ID, repository identity, or distinctive named feature.
- Their normalized titles are exact matches and their event times are compatible.

Title similarity and a time window may support a merge only after a strong event anchor matches. They are never sufficient by themselves. Generic terms such as `release`, `update`, `new`, `agent`, and `model` are not anchors.

Two items must not merge merely because they concern the same entity within 72 hours. When uncertain, keep candidates separate: duplicate recommendations are preferable to combining two distinct events.

### Freshness

Use the best credible publication or release timestamp, falling back to first-observed time when necessary.

```text
Freshness = 100 × 2 ^ (-age_hours / 48)
```

This gives a 48-hour half-life while allowing important week-old events to appear. Reports always show the raw timestamp and age.

### Evidence strength

Assign a fixed value from observable provenance:

- `100`: authoritative primary announcement plus independent confirmation.
- `90`: authoritative official release or changelog entry.
- `80`: at least two independent non-official source families.
- `55`: one relevant non-official source family.

### Interest band

Interest is a label backed by raw evidence rather than a claimed calibrated momentum score. Provider modules collect measurements but do not assign bands. One centralized ranking component loads all thresholds from TOML configuration, evaluates the measurements, and records the exact rule that triggered the band.

The shipped starting defaults are:

```toml
[ranking.interest]
strong_hn_points = 100
strong_hn_comments = 50
moderate_hn_points = 20
moderate_hn_comments = 10

strong_github_observed_star_delta = 50
moderate_github_observed_star_delta = 5
exploratory_repo_max_age_days = 30
strong_exploratory_repo_stars = 500
moderate_exploratory_repo_stars = 50

strong_huggingface_likes = 100
moderate_huggingface_likes = 20
strong_huggingface_trending_rank = 25
moderate_huggingface_trending_rank = 100

strong_source_family_count = 3
moderate_source_family_count = 2
```

A candidate is `strong` when any configured strong threshold is met, `moderate` when any configured moderate threshold is met, and otherwise `early/limited`. Missing observations are `unavailable`, not zero.

These values are uncalibrated starting heuristics. They must be tunable after real scans without code changes. The effective thresholds, configuration fingerprint, scoring version, raw measurements, and triggering rule are persisted with every scan and shown in reports.

Internal ordering values are:

- Strong: `100`.
- Moderate: `60`.
- Early/limited: `25`.

### Discovery Priority

```text
Discovery Priority = 0.60 × Freshness
                   + 0.25 × EvidenceStrength
                   + 0.15 × InterestValue
```

The weighting favors genuinely recent events and authoritative releases. Discovery Priority is an explainable discovery-ordering heuristic, not a prediction of virality or a YouTube opportunity score. Every component, threshold, raw input, triggering rule, and scoring version appears in the report.

YouTube evidence is deliberately excluded from V1 Discovery Priority. The operator inspects it before deciding to create a video. This avoids an unsupported or falsely precise automatic crowding judgment while keeping the radar useful when YouTube credentials or quota are unavailable.

## 6. Cold-Start Behavior

The first scan must rank useful candidates without historical observations. It can immediately use:

- Official-source authority and event age.
- Current HN points and comments.
- Repository creation/update age and current stars.
- Current Hugging Face likes, downloads, and supported trending metadata.
- Number of independent source families.
- First-observed time and supporting links.

At first observation, deltas are explicitly `unavailable` or zero-duration—not historical estimates. Later scans automatically add:

- GitHub observed star change and observation duration.
- HN observed point/comment change.
- Hugging Face observed metric change.

V1 will not display a calibrated `Momentum` score. It displays an interest band plus the underlying observations.

First observation never makes an old watched repository fresh. Repository snapshots support genuine release candidates and remain in storage until enough observation history exists for a configured growth trigger.

## 7. YouTube Validation

Before YouTube validation, selected release events receive one deterministic primary video angle and up to two alternatives. The release remains the single ranked event and retains its original Discovery Priority. Extraction favors developer-facing capabilities, commands, workflows, integrations, APIs, important behavior changes, availability, and material breaking/security/performance changes. Configurable noise terms exclude routine bug fixes, dependency bumps, documentation, refactors, and maintenance. Exact supporting bullets, parent release identity, extraction version, specificity, and fallback reason are persisted in the report.

After scoring, topicability partitions presentation without modifying the underlying ranking calculation. A release with only a low-specificity product/version fallback moves to **Release Watch**, and the next actionable ranked candidate fills the main list. The event, score, extraction result, exclusion reason, and evidence remain in the report. A low-specificity release stays in the main list only when it has multiple independent source families or meets an existing configured `strong` Interest rule. No separate topicability score is calculated.

Validate only the top ten candidates by default. Generate no more than two searches per candidate. For release events, construct viewer intent from the extracted primary and alternative angles rather than the raw repository/version title. Do not expose GitHub `owner/repository` syntax or add generic `tutorial` modifiers to release/update/news queries. If the metadata contains no defensible meaningful change, retain a version-based release query and explicitly label it as low-specificity intent.

Compress high-specificity angle queries to the product plus the minimum grounded technical subject/object terms, normally three to seven words total. Remove filler verbs and descriptive changelog prose without introducing synonyms or unsupported capabilities. Omit versions when the feature terms are distinctive; retain them for ambiguous or fallback intent.

For non-release events and projects, use the human-readable event or project identity plus its distinctive terms. Avoid a broad product-only query whenever the event supplies a more precise intent.

Defaults:

- Recent 30-day publication window.
- Up to ten results per query.
- Video results only, relevance ordering, and English relevance-language preference.
- Deduplicate video IDs across queries.
- Order evidence by supported query relevance and recency behavior.
- Cache successful searches for six hours.
- Enforce a configurable per-scan request budget.

The compact viewer intent is the exact query sent to YouTube; V1 does not accumulate ad hoc negative keywords in an attempt to post-correct fuzzy search. YouTube-returned video order and content are preserved. Optional deterministic title/channel relevance annotations are additive, explicitly labeled as client analysis, never used to filter or reorder results, and disabled by default behind an operator-controlled policy gate. Enabling them requires the operator to accept and comply with YouTube's applicable derived-metrics amendment.

The report shows:

- Exact queries used.
- The human-readable primary release topic and optional alternative angles with exact release-note evidence.
- Viewer-intent type, basis, and specificity, including an explicit low-specificity label for version-only releases.
- Recent videos with titles, channels, publication ages, URLs, durations, and supported statistics.
- Whether validation succeeded, was disabled, used cache, was stale, or failed.
- A direct YouTube search link for manual inspection.
- An explicit notice that YouTube evidence did not affect Discovery Priority.
- Whether client-generated relevance annotations were disabled or explicitly enabled, with a policy link and preservation notice.

V1 does not produce an exact competition count, crowding score, quality judgment, Shorts classification, views-per-hour metric, major-creator label, or claim that an intent is fully served.

## 8. Reliability and Error Strategy

- Run discovery providers independently and concurrently.
- Catch errors at provider boundaries so one failure never aborts another provider.
- Apply explicit connection and response timeouts.
- Retry rate limits and transient `5xx` failures at most twice, honoring `Retry-After` when supplied.
- Use ETags and last-modified validators where supported.
- Fall back to unexpired cache, then stale cache when useful, with visible timestamps and status.
- Commit each provider's persisted batch transactionally.
- Write reports atomically.
- Never log tokens, API keys, authorization headers, or unsanitized response bodies.

A scan succeeds when at least one discovery provider produces usable live or cached data and both persistence and report generation succeed.

A scan fails when:

- Every discovery provider is unavailable and no usable cache exists; or
- SQLite persistence or report writing fails.

YouTube failure never prevents discovery results from being reported.

Every report identifies:

- Provider status.
- Fetch or cache age.
- Sanitized errors.
- Missing evidence.
- Confidence limitations caused by unavailable providers.

## 9. Deliberately Deferred

V1 will not include:

- Calibrated momentum or acceleration models.
- Automatic YouTube crowding or overall opportunity scores derived from YouTube data.
- Generic HTML scraping infrastructure.
- Historical reconstruction of GitHub stars.
- Long-lived topic graphs or complex event lineage.
- Raw-response archives or a full replay framework.
- Automated multi-week evaluation or backtesting.
- LLM calls, embeddings, vector databases, or semantic clustering.
- Web dashboards, hosting, queues, workers, daemons, or notifications.
- Reddit, Product Hunt, Google Trends, X/Twitter, or paid providers.
- YouTube transcripts, comments, creator-tier classification, or content-quality scoring.
- ORM layers, migration frameworks, or an elaborate provider plugin system.

These are potential improvements only after real V1 scans expose a concrete need.

## 10. One-Pass Implementation Sequence and Suggested Commits

These are implementation checkpoints, not separate approval gates. The complete V1 should be implemented and validated in one working pass.

1. `chore: establish python cli config and sqlite storage`
   Package skeleton, CLI, TOML/env loading, schema, cache, logging, and `doctor`.

2. `feat: collect live discovery signals`
   Official feeds, watched/exploratory GitHub, HN, Hugging Face, provider isolation, and snapshots.

3. `feat: normalize deduplicate and rank candidates`
   Common item model, anchor-first deduplication, relevance rules, freshness, configurable evidence bands, and Discovery Priority.

4. `feat: add youtube evidence and reports`
   Query generation, supported YouTube metadata, quota controls, Markdown/JSON rendering, and source links.

5. `test: verify complete end-to-end radar scan`
   Mocked integration test, opt-in live smoke test, fixtures, README, `.env.example`, example configuration, and attribution.

The first live integrated scan remains visible as its own validation result. Problems found during it are fixed in a subsequent commit rather than hiding the initial result.

## 11. Tests and Acceptance Criteria

Automated tests cover:

- UTC timestamp parsing and age calculation.
- Relevance inclusion/exclusion rules.
- Generic company/legal news rejection and summary-only entity non-assignment.
- Static repository snapshots remaining support-only and retaining their creation timestamp.
- New-repository and mature observed-growth trigger behavior.
- Weak community-only eligibility boundaries and later qualification.
- URL normalization and anchor-first conservative deduplication.
- Same-entity releases within 72 hours remaining separate without a shared event anchor.
- Shared version, model, repository, or distinctive feature anchors merging compatible source items.
- Generic keyword overlap not causing a merge.
- Freshness decay.
- Interest thresholds loading from configuration, including below/at/above boundary cases.
- Changing threshold configuration changing interest classification without code changes.
- Missing measurements remaining unavailable instead of becoming zero.
- Evidence levels, interest bands, triggering rules, and ranking order.
- Repeated-observation deltas and cold-start labels.
- YouTube query generation and video deduplication.
- Discovery Priority remaining unchanged when YouTube evidence changes or fails.
- HTTP caching, stale fallback, retry behavior, and provider failure isolation.
- Deterministic Markdown and JSON rendering.
- A fully mocked end-to-end scan using a temporary SQLite database.

Live network tests are opt-in so the default suite is deterministic, fast, and credential-free.

V1 is complete when:

- A clean clone can be installed using documented steps.
- `scan` attempts all configured discovery providers.
- All six provider responsibilities are implemented against live supported sources.
- One unavailable provider produces a partial report rather than a failed scan.
- A first scan ranks candidates without invented historical momentum.
- A second scan reports observed aggregate deltas.
- Markdown and JSON show timestamps, signals, effective eligibility/interest thresholds, triggering rules, Discovery Priority, provider status, missing evidence, explanations, and links.
- With a valid YouTube key and quota, recent competing videos appear for top candidates.
- Without YouTube access, discovery and ranking still work and the limitation is explicit.
- Tests do not require secrets or live network access.
- README, `.env.example`, configuration examples, scoring rules, source attribution, and limitations are understandable to another developer.

## 12. Blockers and External Constraints

No known architectural blocker prevents a complete one-pass implementation.

External constraints:

- A YouTube Data API key and available quota are required to demonstrate live YouTube evidence. The tool remains useful without them.
- A GitHub token is strongly recommended because anonymous limits are restrictive. Anonymous operation may be partial or slower.
- A Hugging Face token may improve reliability but is not mandatory for public data.
- Official feeds and API response shapes can change; starter sources must be validated during implementation.
- Rate limits and temporary outages may force cached or partial results.

These affect live evidence availability, not the ability to build or test the complete system.

## 13. Approved Assumptions

- Python 3.12 is the supported baseline.
- The package uses an MIT license unless repository requirements later say otherwise.
- Default output is global, English-oriented, and focused on ten recommendations.
- YouTube is evidence-only and does not alter V1 Discovery Priority.
- Interest thresholds are configuration-driven starting heuristics, not calibrated truths.
- The effective thresholds and triggering rules are persisted with each scan so tuning remains reproducible.
- This planning update changes only `PLAN.md`; implementation begins only after a separate explicit request.
