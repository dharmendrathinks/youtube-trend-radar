# Contributing

Thanks for helping improve YouTube Trend Radar. Keep contributions focused, explainable, and easy to verify.

## Setup

```bash
git clone https://github.com/dharmendrathinks/youtube-trend-radar.git
cd youtube-trend-radar
cp config.example.toml config.toml
cp .env.example .env
uv sync --extra dev
```

No credentials are needed for the automated test suite.

## Before opening a pull request

```bash
uv run pytest
uv build
```

For changes involving live providers, also run `uv run youtube-trend-radar doctor` and an appropriate opt-in smoke test. Never include credentials, `.env`, `config.toml`, local databases, caches, or generated live reports in a commit or issue.

Pull requests should:

- Describe the problem and the observable behavior change.
- Add or update tests for normalization, eligibility, scoring, query generation, or reporting behavior affected by the change.
- Preserve provider failure isolation and credential-free tests.
- Keep calculations deterministic and evidence-backed.
- Update public documentation when configuration or output changes.

Scoring weights and eligibility thresholds are product behavior. Do not casually tune them to improve one scan; propose such changes with evidence, before/after examples, and regression tests.

Keep commits small enough to review, but do not rewrite authentic development history solely for cosmetic reasons.

