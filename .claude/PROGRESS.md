# PROGRESS.md — Session & Task Tracker
# FinTrack — Market Intelligence Platform
# ========================================
#
# READING INSTRUCTIONS (for Claude):
#   1. Read CURRENT STATE — this is the only section you act on immediately.
#   2. Consult SPRINT BACKLOG for the full task list.
#   3. SESSION LOG is historical — read only to understand prior decisions.

---

## ⚡ CURRENT STATE
> Rewritten at the end of every session. Single source of truth for RIGHT NOW.

**Last updated:** 2026-03-06 — Session 000 (initial setup)
**Active sprint:** Sprint 1 — Django Scaffold & Data Foundation
**Overall status:** 🟡 Ready to build — all decisions resolved

### What was just completed
- All architectural decisions resolved (DEC-001 through DEC-006)
- Claude Code context system initialised (CLAUDE.md, PROGRESS.md, ARCHITECTURE.md, DECISIONS.md, commands)
- Free hosting stack confirmed: Render.com + Neon + Upstash + Vercel

### What to work on NEXT (in order)
1. [ ] **Django project scaffold** — `django-admin startproject config backend/`, split settings into `base.py` / `dev.py` / `prod.py`, install DRF + core deps, health endpoint `GET /api/health/`
2. [ ] **Database setup** — create Neon free project, set `DATABASE_URL` in `.env`, run `python manage.py migrate`, verify connection
3. [ ] **Docker Compose (local dev)** — `postgres:15` + `redis:7` services, `.env.example` with all required vars documented

### Active blockers
- None — all decisions resolved, ready to build

### Session notes
- All ingestion intervals must be ≥ 5 minutes (Upstash 10k commands/day limit)
- Use `rediss://` (TLS) for Upstash Redis URL — plain `redis://` will be refused
- Django settings split is mandatory from day one — avoid single `settings.py`
- `requirements-ml.txt` is Phase 2 only — never reference it in Phase 1

---

## 📋 SPRINT BACKLOG

---

### Sprint 1 — Django Scaffold & Data Foundation
**Goal:** Live market prices from yfinance flowing into Neon PostgreSQL, queryable via `/api/prices/`
**Scope:** Phase 1 start

#### Milestone 1A — Project Scaffold
- [ ] Django 4.2 project init: `django-admin startproject config backend/`
- [ ] Split settings: `config/settings/base.py`, `dev.py`, `prod.py`
- [ ] Install and configure: `djangorestframework`, `dj-database-url`, `whitenoise`, `django-cors-headers`, `celery`, `django-celery-beat`, `redis`, `ruff`, `pytest-django`
- [ ] `requirements.txt` updated and locked
- [ ] `GET /api/health/` endpoint — returns `{"status": "ok", "version": "0.1.0"}`
- [ ] Docker Compose: `postgres:15` + `redis:7` for local dev
- [ ] `.env.example` — all vars documented with descriptions
- [ ] `python manage.py migrate` succeeds against Neon

#### Milestone 1B — Market Data Models
- [ ] `backend/apps/market_data/` Django app created and registered
- [ ] `Asset` model (symbol, name, asset_type, exchange, is_active)
- [ ] `PricePoint` model with composite index on `(asset, -timestamp)` and `unique_together` on `(asset, timestamp)`
- [ ] Migrations created and applied
- [ ] Django admin registered for `Asset` and `PricePoint`
- [ ] 5–10 seed assets added via Django management command (`python manage.py seed_assets`)

#### Milestone 1C — yfinance Ingestion Pipeline
- [ ] `data_pipeline/ingestion/yfinance_fetcher.py` — `fetch_ohlcv(symbol, period, interval)` function using `yfinance`
- [ ] Handles `time.sleep(0.5)` between fetches; uses `yf.download()` for batching
- [ ] Celery app defined in `config/celery.py` — auto-discovered tasks
- [ ] `ingest_prices` Celery task: fetches all active assets, bulk creates `PricePoint` rows (skip duplicates via `update_or_create` or `ignore_conflicts=True`)
- [ ] `django-celery-beat` periodic task: `ingest_prices` every 5 minutes
- [ ] `prune_old_price_points` Celery task: deletes PricePoints older than 90 days
- [ ] Verified: `python manage.py shell` → query PricePoint, see real data

#### Milestone 1D — CoinGecko Crypto Ingestion
- [ ] `data_pipeline/ingestion/coingecko_fetcher.py` — fetches OHLCV for BTC, ETH, top 10 crypto
- [ ] Assets seeded for crypto symbols
- [ ] `ingest_crypto` Celery task: every 5 minutes
- [ ] Verified: BTC-USD price visible in Django admin

#### Milestone 1E — DRF Price API
- [ ] `AssetSerializer`, `PricePointSerializer` in `market_data/serialisers.py`
- [ ] `AssetViewSet` — list, retrieve
- [ ] `GET /api/prices/{symbol}/` — returns last 100 price points for a symbol
- [ ] `GET /api/prices/{symbol}/?from=<date>&to=<date>` — date range filter
- [ ] Django admin cache: 30s cache on price endpoints via `cache_page`
- [ ] Pagination: 100 results per page default

#### Milestone 1F — News Aggregation
- [ ] `data_pipeline/ingestion/rss_fetcher.py` — parses Yahoo Finance RSS with `feedparser`
- [ ] `Article` model: url (unique), headline, source, published_at, related_assets (M2M)
- [ ] `ingest_news` Celery task: every 15 minutes per active asset
- [ ] `GET /api/news/` — paginated, filterable by symbol
- [ ] Verified: articles visible in Django admin

#### Sprint 1 verification checklist
- [ ] `python manage.py test` — all tests pass
- [ ] `ruff check .` — 0 errors
- [ ] `mypy backend/` — 0 errors
- [ ] Docker Compose `up -d` — postgres and redis start cleanly
- [ ] Manual: query `GET /api/prices/AAPL/` — get real price data
- [ ] Manual: query `GET /api/news/?symbol=AAPL` — get news articles
- [ ] Manual: Django admin `/admin/` — see ingested Assets, PricePoints, Articles

---

### Sprint 2 — Auth, Watchlists & Price Alerts
**Goal:** User accounts, watchlists, and price alert system
**Scope:** Phase 1, after Sprint 1 complete

#### Tasks (draft — refine at Sprint 2 start)
- [ ] Custom user model in `apps/users/` (always do this before first migration)
- [ ] JWT auth: `djangorestframework-simplejwt` — register, login, refresh endpoints
- [ ] `Watchlist` model — user-owned list of Assets
- [ ] CRUD API: `GET/POST /api/watchlist/`, `DELETE /api/watchlist/{symbol}/`
- [ ] `PriceAlert` model — threshold, direction (above/below), triggered flag
- [ ] `check_price_alerts` Celery task — runs every 5 min, compares latest price to alert thresholds
- [ ] Email notification via Django's `send_mail` (use Gmail SMTP free tier or Resend free tier)
- [ ] Rate limiting: `djangorestframework-throttle` on all public endpoints

---

### Sprint 3 — React Frontend Dashboard
**Goal:** Interactive dashboard with live prices, charts, and news feed
**Scope:** Phase 1, after Sprint 2 complete

#### Tasks (draft — refine at Sprint 3 start)
- [ ] Vite + React + Tailwind CSS init in `frontend/`
- [ ] Vercel project created and linked to GitHub
- [ ] App shell: sidebar nav, header, responsive layout
- [ ] Dashboard page: market heatmap, top movers, portfolio summary
- [ ] Asset detail page: price chart (TradingView Lightweight Charts), news feed
- [ ] Watchlist UI: add/remove/reorder
- [ ] Price alert UI: create, list, delete
- [ ] Auth pages: login, register (JWT flow)

---

### Sprint 4 — Production Deployment
**Goal:** Fully deployed on free stack, stable and monitored
**Scope:** Phase 1 close

#### Tasks (draft — refine at Sprint 4 start)
- [ ] Render.com: deploy Django web service from GitHub (set env vars, build command, start command)
- [ ] Render.com: deploy Celery background worker
- [ ] Vercel: deploy React frontend (set `VITE_API_URL` to Render URL)
- [ ] cron-job.org: set up free ping to `/api/health/` every 10 min (keep Render awake)
- [ ] `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `DEBUG=False` verified in prod settings
- [ ] Deployment runbook in `docs/deployment.md`

---

### Phase 2 — ML Integration (future — do not start until Phase 1 complete)
- [ ] Sentiment analysis on Article headlines (VADER or FinBERT)
- [ ] Price prediction LSTM / Prophet on PricePoint history
- [ ] ML API endpoints in Django backend
- [ ] Only then install `requirements-ml.txt`

---

## 📅 SESSION LOG
> Append a new entry per session. Never edit old entries.

============================================================
SESSION 000 | DATE: 2026-03-06 | Sprint: Pre-Sprint (Setup)
============================================================

### Completed this session
- All 6 architectural decisions resolved and logged in DECISIONS.md
- Free hosting stack confirmed: Render.com + Neon + Upstash + Vercel
- Django chosen as backend framework (DEC-001)
- yfinance + CoinGecko + FRED + Yahoo RSS chosen as data sources (DEC-003, DEC-006)
- TimescaleDB replaced with composite PostgreSQL indexes (DEC-005)
- Claude Code context system fully initialised

### Key decisions made
- DEC-001: Django 4.2 LTS + DRF (owner preference + admin value)
- DEC-002: React on Vercel (existing frontend/ scaffold, free hosting)
- DEC-003: yfinance + CoinGecko + FRED (all free, no paid API keys)
- DEC-004: Celery + django-celery-beat (native Django, Render worker)
- DEC-005: Standard PostgreSQL indexes (TimescaleDB not on free tiers)
- DEC-006: Yahoo Finance RSS via feedparser (truly free news, no rate limit)

### Problems encountered & solutions
- None

### Next session: start with
1. Run `/project:start`
2. Django project scaffold (Milestone 1A) — startproject, split settings, health endpoint
3. Connect to Neon PostgreSQL — get free project URL, set DATABASE_URL, run migrate

============================================================
END SESSION 000
============================================================
