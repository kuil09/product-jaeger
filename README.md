# Product Jaeger — Early Signal Radar

Product Jaeger is a low-noise early signal radar. It polls Show HN, GitHub Search, and RSS, canonicalizes and clusters repeated sightings, scores momentum and topic fit, and sends a digest of at most 20 candidates twice a day.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install ".[dev]"
pytest
ruff check .
mypy src
```

## Fork and configure

1. Fork `kuil09/product-jaeger` on GitHub and open the fork's `Settings > Actions > General` page.
2. Create a free Neon PostgreSQL project. Copy its pooled connection string and run the first database setup locally with `NEON_DATABASE_URL='...' product-jaeger init-db`, or use the same command from a manually dispatched workflow.
3. Create an OpenRouter API key. Store only the key in GitHub; Product Jaeger discovers the current free model catalog at each digest run, so no model ID needs to be configured.
4. Create a Telegram bot with BotFather, start a chat with it using `/start`, and obtain the numeric chat ID for that chat.
5. Add the four repository secrets listed below under `Settings > Secrets and variables > Actions`. Never put them in YAML, `.env` committed files, issues, or public artifacts.
6. Enable Actions if the fork has them disabled, then run `Ingest radar sources` manually once.
7. Run `Build and deliver digest` manually after ingestion. Keep `RADAR_DRY_RUN=true` for local runs; the production workflow explicitly sets it to false.
8. In `Settings > Pages`, choose `GitHub Actions` as the build and deployment source. `Publish public archive` then deploys the latest recorded digest.

The first Telegram delivery requires that the bot has an active chat with the recipient. A missing chat ID, a stopped bot, or a Telegram error fails delivery after the digest has already been recorded, so the Pages workflow can still publish it.

## GitHub Actions setup

Create a Neon PostgreSQL database, a Telegram bot/chat, and an OpenRouter API key. The digest run reads the current OpenRouter catalog every time, selects a free model that meets the configured reasoning/context threshold, and tries at most three ranked candidates if a provider is rate-limited. Add these repository Actions secrets:

- `NEON_DATABASE_URL`
- `OPENROUTER_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The default selection threshold is `llm_min_context_length: 8192` and `llm_min_capability_score: 7` in `config/config.yml`. The score requires OpenRouter metadata to advertise reasoning support, then rewards structured JSON support, context length, and completion capacity. It is deliberately re-evaluated on each digest run; no model ID is pinned in the repository.

`GITHUB_TOKEN` is provided by Actions. Enable Pages with GitHub Actions as its source. The checked-in config is `dry_run: true`; scheduled production workflows set `RADAR_DRY_RUN=false`.

Workflows:

- `ci.yml`: tests, Ruff, and mypy.
- `ingest.yml`: 15-minute runner with source-specific interval gates.
- `digest.yml`: 07:30 and 21:00 KST digest and Telegram delivery.
- `publish.yml`: public Pages projection with `latest` and date-stamped archive entries.
- `manual-run.yml`: manually selected source and ISO-8601 start-time replay.

For a targeted replay, open `Manual radar run` and choose a source plus an optional ISO-8601 `since` value. The equivalent local command is `product-jaeger ingest --source hn_show --since 2026-09-12T00:00:00Z`.

## Configuration and operating limits

Change topics, excluded domains, source intervals, RSS feeds, score weights, digest size, and model thresholds in `config/config.yml`; `config.example.yml` is a safe starting point for a fork. The digest defaults to 15 entries and never exceeds 20. Source observations are retained in Neon for 90 days; public Pages contains only the allowlisted projection and not raw source JSON, comments, or private feedback.

GitHub scheduled workflows may start late. This is a low-cost periodic personal radar, not a real-time alerting or trading system. GitHub Actions, Neon, OpenRouter free-model quotas, and Telegram limits can change; free inference may be rate-limited and automatically falls back to rules plus a template summary. The expected monthly operating cost is approximately $0–20 when using free tiers, but quotas and paid upgrades are the operator's responsibility.

Respect each provider's API terms, rate limits, robots/access rules, attribution requirements, and license. Do not use this project to bypass login walls or CAPTCHAs, bulk-republish source content, or make automated investment or publishing decisions.

Raw source payloads and credentials are never published. Pages exposes only an allowlisted title, link, source, topics, summary, scores, and timestamps. Do not bypass access controls, login walls, CAPTCHAs, robots rules, or provider terms. Product Hunt, Reddit, Kickstarter, X, and login-only Korean sites are later work.

The code is Apache-2.0; upstream source content remains subject to its own terms.
