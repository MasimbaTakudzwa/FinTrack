# DECISIONS.md — Key Decisions & Rationale
# FinTrack — Market Intelligence Platform
# ==========================================
# NOT auto-loaded. Import on demand: @.claude/DECISIONS.md
#
# Read this BEFORE proposing alternatives to anything below.
# ADD a new entry for every non-obvious decision.
# NEVER delete entries — they explain why certain paths were rejected.

---

## Decision Log

---

### DEC-001 | Backend Framework: Django (resolved)
**Date:** 2026-03-06
**Context:**
The backend serves a REST API consumed by React and hosts Celery workers for
scheduled market data ingestion. Django was selected by the project owner.

**Options considered:**
- FastAPI — async-native, lighter, excellent for high-throughput I/O. Less
  built-in (no admin, no auth, no ORM). Would require more scaffolding.
- **Django 4.2 LTS + DRF (chosen)**

**Rationale:**
- Django admin is a zero-cost data inspection dashboard during development — critical
  for verifying ingested price data without building frontend first
- `django-celery-beat` stores periodic task schedules in the DB, manageable via admin
- DRF provides serialisers, viewsets, and authentication out of the box
- LTS release — supported until April 2026 (upgrade path to Django 5.x is clear)
- Owner preference

**Consequences:**
- Sync-first ORM — use `select_related`, `prefetch_related`, and queryset optimisation
  for performance-critical endpoints
- Django settings must be split: `config/settings/base.py`, `dev.py`, `prod.py`
- Use `dj-database-url` to parse `DATABASE_URL` environment variable (Neon, Render)
- Use `whitenoise` for static file serving on Render without a separate CDN

---

### DEC-002 | Frontend Framework: React (resolved)
**Date:** 2026-03-06
**Context:**
Project was originally scoped with React. Free hosting on Vercel is available.
Keeping the existing `frontend/` scaffold.

**Options considered:**
- Django templates + HTMX — single service, no separate hosting, simpler. Less
  interactive for real-time chart updates.
- Vue.js — similar ecosystem to React, Pinia is clean, but fewer financial charting
  resources.
- **React (chosen)** — project already has frontend/ folder, owner familiar, Vercel
  free tier is genuinely zero cost, TradingView Lightweight Charts has React examples.

**Rationale:**
React on Vercel eliminates frontend hosting cost. DRF provides the API contract.
CORS configured in Django to allow requests from Vercel domain.

**Consequences:**
- Install `django-cors-headers` — add Vercel deployment URL to `CORS_ALLOWED_ORIGINS`
- Two separate deploy targets: Render (Django) and Vercel (React)
- React build artifacts never committed to Django repo — separate Vercel project

---

### DEC-003 | Market Data Sources: yfinance + CoinGecko + FRED (resolved)
**Date:** 2026-03-06
**Context:**
All data sources must be free. Paid APIs (Polygon, IEX Cloud premium) are out of scope.
The project may build custom fetchers if free tiers are insufficient.

**Options evaluated:**

| Source | Cost | Rate limit | Covers |
|--------|------|-----------|--------|
| yfinance | Free, no key | Informal (be polite) | Stocks, ETFs, crypto, forex, historical |
| CoinGecko API | Free, no key | 10–50 calls/min | Crypto only, excellent |
| FRED API | Free, key required | 120 calls/min | Macro indicators (CPI, GDP, rates) |
| Alpha Vantage | Free key | 25 calls/day | Stocks, ETFs, crypto |
| Yahoo Finance RSS | Free | None stated | News headlines only |
| NewsAPI.org | Free dev key | 100 calls/day | General financial news |

**Chosen combination:**
- **yfinance** — primary source for stocks, ETFs, crypto OHLCV + historical data
- **CoinGecko** — supplemental crypto data and pricing (no throttle risk)
- **FRED API** (free key from fred.stlouisfed.org) — macro economic indicators
- **Yahoo Finance RSS** — news headlines, fully free, no API key

**Rationale:**
yfinance wraps Yahoo Finance and handles session management. It supports batch
downloads (`yf.download(tickers=[...])`) which reduces request count significantly.
CoinGecko provides clean crypto data independently of Yahoo. FRED covers economic
context (interest rates, inflation) that adds value to a market platform.

**Consequences:**
- Add `time.sleep(0.5)` between individual yfinance symbol fetches to avoid throttling
- All fetchers in `data_pipeline/ingestion/` — one file per source
- `FRED_API_KEY` required in `.env` — add to `.env.example` with instructions
- If yfinance breaks (Yahoo changes API): fallback to Alpha Vantage free key (25/day)
  or build a custom scraper against public endpoints

---

### DEC-004 | Task Scheduling: Celery + django-celery-beat (resolved)
**Date:** 2026-03-06
**Context:**
Market data must be fetched on a recurring schedule. Need a solution that works
on the free hosting stack (Render.com background worker).

**Options considered:**
- Apache Airflow — too heavy, requires dedicated infra, overkill for Phase 1
- Prefect — better than Airflow but still a separate service to host
- **Celery + django-celery-beat (chosen)** — native Django integration, schedules
  stored in PostgreSQL, manageable through Django admin, runs on Render free worker

**Rationale:**
django-celery-beat stores periodic task schedules in the Django DB. This means
ingestion schedules can be adjusted via Django admin without code changes.
Celery runs as a separate Render.com background worker process (free tier: 1 worker).

**Consequences:**
- Celery app defined in `config/celery.py`, auto-discovered via `config/__init__.py`
- Upstash Redis as broker — configure via `CELERY_BROKER_URL` env var
  (Upstash provides a Redis URL with `rediss://` TLS — required for Upstash)
- Keep task intervals ≥ 5 minutes to stay within Upstash 10k commands/day free limit
- Production: two Render services — web (Django) + worker (Celery)

---

### DEC-005 | Time-Series Storage: Standard PostgreSQL indexes (revised)
**Date:** 2026-03-06
**Context:**
Original plan used TimescaleDB for price time-series. Neon (free PostgreSQL hosting)
does not support the TimescaleDB extension. TimescaleDB Cloud's free trial is 90 days
— not a sustainable free solution.

**Original choice:** TimescaleDB hypertables
**Revised choice:** Standard PostgreSQL with optimised composite indexes

**Rationale:**
For Phase 1 data volumes (a few hundred symbols, 5-minute bars, 90-day retention),
standard PostgreSQL with proper indexing performs well within Neon's free tier.
A composite index on `(asset_id, timestamp DESC)` covers the primary query pattern.
TimescaleDB can be re-evaluated if Phase 2 data volumes require it.

**Consequences:**
- `PricePoint` model: add `class Meta: indexes = [models.Index(fields=['asset', '-timestamp'])]`
- Implement 90-day data retention via a Celery task (`prune_old_price_points`)
  to stay within Neon 0.5GB storage limit
- Query pattern: always filter by `asset` first, then `timestamp` range
- Do NOT use plain `postgres:latest` Docker image for local dev — use `postgres:15`
  (standard, no extension needed)

---

### DEC-006 | News Aggregation: Yahoo Finance RSS (resolved)
**Date:** 2026-03-06
**Context:**
NewsAPI.org free tier is limited to 100 requests/day and news is delayed by 24h on
the free plan. Need a truly free, real-time news source.

**Options considered:**
- NewsAPI.org — 100 req/day, 24h delay on free tier
- Google News RSS — free, but ToS is ambiguous for automated scraping
- **Yahoo Finance RSS (chosen)** — publicly available RSS feeds per symbol and category,
  no key, no rate limit stated, structured XML easy to parse
- SEC Edgar RSS — free, useful for regulatory filings (Phase 2)

**Chosen:** Yahoo Finance RSS as primary news source. Parse with `feedparser` library.
Build a custom `RSSFetcher` in `data_pipeline/ingestion/news_fetcher.py`.

**Consequences:**
- `pip install feedparser` added to `requirements.txt`
- RSS endpoint pattern: `https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US`
- Store articles in `Article` model with deduplication on `url` field (`unique=True`)
- Celery task: fetch news every 15 minutes per watchlisted symbol

---

<!-- Template for new entries:

### DEC-NNN | [Short title]
**Date:** YYYY-MM-DD
**Context:**
**Options considered:**
- Option A:
- **Chosen:**
**Rationale:**
**Consequences:**

-->
