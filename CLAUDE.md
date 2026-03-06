# FinTrack — Claude Code Project Memory
# =======================================
# Auto-loaded every session. Keep under 200 lines.
# Run `/project:start` at the beginning of every session.
# Run `/project:end` before closing every session.

---

## Project Identity

- **Name:** FinTrack — Market Intelligence Platform
- **Purpose:** Real-time market data tracking (stocks, ETFs, commodities, crypto) with news aggregation, portfolio tools, and Phase 2 ML price prediction
- **Stack:** Django 4.2 LTS + DRF / React / PostgreSQL (Neon) / Upstash Redis / Celery / Docker (local dev) / Render.com + Vercel (hosting)
- **Repo root:** `~/projects/FinTrack`  ← update to your local path
- **Current phase:** Phase 1 — Market Tracking Platform

---

## Free Hosting Stack (zero cost)

| Layer | Service | Notes |
|-------|---------|-------|
| Django backend | Render.com (free web service) | Spins down after 15min inactivity |
| Celery worker | Render.com (free background worker) | 1 free worker |
| PostgreSQL | Neon (free tier, 0.5GB) | Serverless, no cold start on DB |
| Redis | Upstash (free tier, 256MB) | 10k commands/day — Celery broker + cache |
| React frontend | Vercel (free) | Unlimited deploys |
| Market data | yfinance + CoinGecko + FRED | No API keys needed (FRED: free key) |

---

## Quick-Start Commands (local dev)

```bash
# First time
./install-fintrack.sh
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver

# Celery (separate terminals)
celery -A config worker -l info
celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler

# Frontend
cd frontend && npm install && npm run dev

# Tests + lint
python manage.py test
ruff check . && mypy backend/
```

---

## Project Structure

```
FinTrack/
├── CLAUDE.md                        ← this file (auto-loaded)
├── .claude/
│   ├── PROGRESS.md                  ← READ THIS FIRST every session
│   ├── ARCHITECTURE.md              ← deep design (@import on demand)
│   ├── DECISIONS.md                 ← decisions log (@import on demand)
│   └── commands/
│       ├── session-start.md         ← /project:start
│       ├── session-end.md           ← /project:end
│       └── checkpoint.md            ← /project:checkpoint
├── backend/
│   ├── config/                      ← Django settings (base/dev/prod), urls, celery app
│   └── apps/
│       ├── market_data/             ← Asset, PricePoint models + DRF API
│       └── news/                    ← Article model + DRF API
├── data_pipeline/
│   ├── ingestion/                   ← yfinance + CoinGecko + FRED fetchers
│   └── processing/                  ← cleaning, normalisation, dedup
├── ml_models/                       ← Phase 2 ONLY — do not touch in Phase 1
├── frontend/                        ← React app (Vercel)
├── infrastructure/                  ← Docker Compose (local dev only)
├── scripts/                         ← management scripts
├── tests/
├── docs/
├── requirements.txt                 ← core Django + pipeline deps
├── requirements-dev.txt             ← dev/test only
└── requirements-ml.txt              ← Phase 2 ONLY — never install in Phase 1
```

---

## Active Sprint

> **Sprint 1 — Data Foundation**
> Goal: Live market prices from yfinance into Neon PostgreSQL, queryable via `/api/prices/`
> Tracking: @.claude/PROGRESS.md

---

## Critical Rules

1. **Read `.claude/PROGRESS.md` before writing any code.**
2. **Never install `requirements-ml.txt` — Phase 2 only.**
3. **All market data fetching lives in `data_pipeline/ingestion/` — never fetch inside Django views.**
4. **Mark tasks [x] only after `python manage.py test` passes.**
5. **Run `/project:checkpoint` after every 2-3 completed tasks.**
6. **Run `/project:end` before ending any session.**
7. **Django settings: always use split config — `config/settings/base.py`, `dev.py`, `prod.py`.**
8. **Never commit `.env` — only `.env.example`. All secrets via environment variables.**

---

## Architecture at a Glance

> Full details: @.claude/ARCHITECTURE.md

- **Backend:** Django 4.2 + DRF — single project in `backend/`, split settings per environment
- **Data sources:** `yfinance` (stocks/ETFs/crypto, no key) + `CoinGecko` (crypto, no key) + `FRED` (macro indicators, free key) — all free
- **Scheduling:** Celery + `django-celery-beat` — periodic tasks stored in DB, managed via Django admin
- **Database:** Neon free PostgreSQL — composite `(asset_id, timestamp)` indexes in place of TimescaleDB
- **Cache/Broker:** Upstash Redis free tier
- **Frontend:** React on Vercel, consuming DRF API

---

## Known Gotchas

- **Render.com free tier sleeps after 15min inactivity** — first request is slow (~30s). Use cron-job.org (free) to ping `/api/health/` every 10 min in production
- **Upstash 10k commands/day** — keep Celery ingestion intervals ≥ 5 minutes on free tier
- **yfinance throttling** — add `time.sleep(0.5)` between symbol fetches; use `yf.download(tickers=[...])` for batching
- **Neon 0.5GB storage** — implement 90-day data retention on `price_points` to stay within limits
- **No TimescaleDB** — composite index on `(asset_id, timestamp)` handles Phase 1 query load
- **FRED key** — register free at fred.stlouisfed.org → store as `FRED_API_KEY` in `.env`
- **Django admin** — use it as a free data inspection tool during development before frontend is built

---

## Context Management

- Run `/compact` when context hits ~50% (`/context` to check)
- After `/compact`, re-run `/project:start` to re-anchor
- PROGRESS.md survives compaction — always re-read from disk
