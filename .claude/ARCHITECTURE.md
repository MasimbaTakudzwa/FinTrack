# ARCHITECTURE.md — FinTrack Deep Architecture Reference
# =========================================================
# NOT auto-loaded. Import on demand: @.claude/ARCHITECTURE.md
# Reference when: designing services, changing DB schema, adding infra.

---

## System Overview

FinTrack is a two-phase market intelligence platform built entirely on a free hosting
stack. Phase 1 delivers real-time price tracking for stocks, ETFs, commodities, and
crypto — plus financial news aggregation and a user portfolio/watchlist dashboard.
Phase 2 adds ML-driven price prediction and sentiment analysis on top of the Phase 1
data layer.

Django serves as the single backend — handling REST API, admin, auth, and Celery task
orchestration. All external data fetching is free (yfinance, CoinGecko, FRED, Yahoo RSS).

---

## Services & Components

### Django Backend
- **Framework:** Django 4.2 LTS + Django REST Framework
- **Location:** `backend/`
- **Settings:** `backend/config/settings/base.py` + `dev.py` + `prod.py`
- **Local port:** 8000
- **Production:** Render.com free web service
- **Key Django apps:**
  - `backend/apps/market_data/` — Asset, PricePoint models + DRF ViewSets + serialisers
  - `backend/apps/news/` — Article model + DRF API
  - `backend/apps/users/` — Custom user model, auth, Watchlist, PriceAlert (Sprint 2)
- **Static files:** WhiteNoise middleware (no CDN needed on free tier)
- **CORS:** `django-cors-headers` — Vercel origin whitelisted in prod settings

### Celery Task Queue
- **Broker:** Upstash Redis (free tier — use `rediss://` TLS URL from Upstash dashboard)
- **Scheduler:** `django-celery-beat` — periodic task schedules stored in PostgreSQL, editable via Django admin
- **Production:** Render.com free background worker (`celery -A config worker -l info`)
- **Key tasks:**
  - `ingest_prices` — runs every 5 min — fetches OHLCV from yfinance for all tracked assets
  - `ingest_crypto` — runs every 5 min — fetches crypto prices from CoinGecko
  - `ingest_news` — runs every 15 min — fetches Yahoo Finance RSS per symbol
  - `prune_old_price_points` — runs daily — deletes price data older than 90 days

### Database
- **Engine:** PostgreSQL 15
- **Production:** Neon free tier (0.5GB, serverless)
- **Local dev:** `postgres:15` Docker container
- **ORM:** Django ORM
- **Migrations:** Django migrations (`python manage.py makemigrations && migrate`)
- **Connection:** `dj-database-url` parses `DATABASE_URL` env var
- **Key models:**

```python
# market_data/models.py
class Asset(Model):
    symbol    = CharField(unique=True)        # e.g. "AAPL", "BTC-USD"
    name      = CharField()                   # e.g. "Apple Inc."
    asset_type = CharField(choices=[...])     # stock | etf | crypto | commodity
    exchange  = CharField()
    is_active = BooleanField(default=True)

class PricePoint(Model):
    asset     = ForeignKey(Asset)
    timestamp = DateTimeField(db_index=True)
    open      = DecimalField()
    high      = DecimalField()
    low       = DecimalField()
    close     = DecimalField()
    volume    = BigIntegerField()

    class Meta:
        indexes = [models.Index(fields=['asset', '-timestamp'])]
        unique_together = [['asset', 'timestamp']]

# news/models.py
class Article(Model):
    url       = URLField(unique=True)         # deduplication key
    headline  = CharField()
    source    = CharField()
    published_at = DateTimeField(db_index=True)
    related_assets = ManyToManyField(Asset, blank=True)
    sentiment_score = FloatField(null=True)   # Phase 2 — nullable for now
```

### Cache
- **Engine:** Upstash Redis (free: 256MB, 10k commands/day)
- **URL format:** `rediss://...` (TLS — Upstash requires this)
- **Used for:**
  - Django cache framework (`CACHE_MIDDLEWARE_SECONDS = 30` for price endpoints)
  - Celery broker and result backend
  - Rate limiting (Sprint 2)

### Data Pipeline
- **Location:** `data_pipeline/`
- **Structure:**
  ```
  data_pipeline/
  ├── ingestion/
  │   ├── yfinance_fetcher.py    ← stocks, ETFs, crypto via yfinance
  │   ├── coingecko_fetcher.py   ← crypto via CoinGecko REST API
  │   ├── fred_fetcher.py        ← macro indicators via FRED API
  │   └── rss_fetcher.py         ← news via Yahoo Finance RSS (feedparser)
  └── processing/
      ├── normaliser.py          ← standardise OHLCV data across sources
      └── deduplicator.py        ← avoid re-inserting existing price points
  ```
- **Celery tasks** call fetchers → pass data to processing → bulk_create into Django ORM

### Frontend
- **Framework:** React (Vite or Create React App)
- **Location:** `frontend/`
- **Production:** Vercel (free, auto-deploy from GitHub)
- **Charts:** TradingView Lightweight Charts (free, MIT licence) or Recharts
- **Styling:** Tailwind CSS
- **State:** Zustand (lightweight, no boilerplate)
- **API calls:** Axios or fetch — against Render.com Django backend URL

### Infrastructure (local dev only)
- **Docker Compose services:** `django`, `postgres`, `redis`
- **Production:** No Docker on Render/Vercel — each platform builds natively
- **Local start:** `docker compose up -d` starts postgres + redis; Django + Celery run locally

---

## Data Flow

```
yfinance / CoinGecko / FRED / Yahoo RSS
           ↓  (Celery periodic tasks every 5–15 min)
data_pipeline/ingestion/  →  data_pipeline/processing/
           ↓  (Django ORM bulk_create)
Neon PostgreSQL  ←→  Upstash Redis (hot cache, Celery broker)
           ↓
Django REST Framework API  (Render.com)
           ↓
React Frontend  (Vercel)
           ↓
User browser
```

---

## Free Tier Limits & Mitigation

| Limit | Mitigation |
|-------|-----------|
| Neon 0.5GB storage | 90-day data retention, prune daily via Celery |
| Upstash 10k cmds/day | Celery intervals ≥ 5 min, cache DRF responses 30s |
| Render free service sleeps after 15min | Ping `/api/health/` every 10 min via cron-job.org (free) |
| Render 1 free background worker | All Celery tasks share 1 worker — keep tasks short |
| yfinance informal rate limits | `time.sleep(0.5)` between fetches, batch with `yf.download()` |

---

## Phase 2 ML (future — do not start in Phase 1)

- Sentiment analysis on `Article.headline` using FinBERT or VADER
- LSTM / Prophet price prediction on `PricePoint` history
- Separate ML service or integrated Django management command
- `requirements-ml.txt` — install only when Phase 2 begins
- `ml_models/training/` — training pipelines, model artefacts

---

## Environment Variables Reference

```bash
# Django
SECRET_KEY=...
DEBUG=False                         # True in dev only
ALLOWED_HOSTS=...                   # Render domain in prod
DATABASE_URL=postgresql://...       # Neon connection string
REDIS_URL=rediss://...              # Upstash TLS Redis URL

# CORS (prod only)
CORS_ALLOWED_ORIGINS=https://your-app.vercel.app

# Data sources
FRED_API_KEY=...                    # free at fred.stlouisfed.org

# Celery (same Redis URL)
CELERY_BROKER_URL=${REDIS_URL}
CELERY_RESULT_BACKEND=${REDIS_URL}
```
