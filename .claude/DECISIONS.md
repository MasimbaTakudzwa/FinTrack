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

### DEC-001 | Backend Framework: Django (superseded)
**Superseded by:** DEC-007 (desktop pivot) + DEC-009 (FastAPI for Python sidecar) — 2026-04-21
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

### DEC-002 | Frontend Framework: React (partially superseded)
**Superseded by:** DEC-008 (React still chosen, but now renders inside a Tauri webview — not deployed to Vercel) — 2026-04-21
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

### DEC-004 | Task Scheduling: Celery + django-celery-beat (superseded)
**Superseded by:** DEC-011 (APScheduler in-process — no broker needed for desktop) — 2026-04-21
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

### DEC-005 | Time-Series Storage: Standard PostgreSQL indexes (superseded)
**Superseded by:** DEC-010 (SQLite with composite index — PostgreSQL dropped entirely) — 2026-04-21
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

### DEC-007 | Architecture Pivot: Web App → Desktop App
**Date:** 2026-04-21
**Context:**
The original plan (DEC-001 through DEC-006) designed FinTrack as a distributed web application: Django backend on Render.com, Celery worker on Render, PostgreSQL on Neon, Redis on Upstash, React frontend on Vercel. When the project resumed on 2026-04-21 after a long pause, the owner requested migrating to a cross-platform desktop application matching the form factor of other projects currently in development.

**Options considered:**
- **Keep as web app with free hosting** — established path, browser access from any device. But: Render's 15-min cold-start penalty on free tier, 10k commands/day Upstash ceiling, 0.5GB Neon cap, 4 hosting providers to coordinate, auth / CORS / rate-limiting complexity, and overall complexity-cost of a distributed system for a single-user tool.
- **Migrate to single-user desktop app (chosen)** — one installer per OS, local SQLite, no cloud, no hosting, no cold starts, user owns their data. Better fit for "tool for me" use case.

**Rationale:**
- Zero hosting cost (not just "free tier" — literally zero)
- No cold-start latency when opening the app
- No per-day API budget concerns (Upstash command ceiling gone)
- Full historical data retention is cheap (local disk, no 0.5GB cap)
- Private by default — watchlists, alerts, portfolios stay on the user's machine
- Simpler architecture: no CORS, no auth, no rate limiting, no distributed state
- Phase 2 ML is strictly better on-device (no inference cost, no latency, no privacy concerns)

**Consequences:**
- Supersedes DEC-001 (Django), DEC-004 (Celery), DEC-005 (Postgres on Neon) — see DEC-009, DEC-011, DEC-010 respectively
- DEC-002 partially superseded — React is still the UI framework but it renders inside a Tauri webview, not a Vercel deployment (see DEC-008)
- DEC-003 (data sources: yfinance + CoinGecko + FRED) and DEC-006 (Yahoo RSS) remain valid
- Entire `backend/` (Django), `infrastructure/` (Docker/K8s/nginx/Terraform), `scripts/` (deploy tooling), and prior `frontend/` scaffolds removed from repo
- Packaging & distribution becomes more complex than web deploy (code signing, auto-updater, per-OS installers) — accepted trade-off
- Multi-device sync is no longer automatic — deferred indefinitely; would require a separate opt-in cloud sync layer

---

### DEC-008 | Desktop Framework: Tauri v2
**Date:** 2026-04-21
**Context:**
DEC-007 committed to a desktop app. Picked a cross-platform desktop framework.

**Options considered:**
- **Electron** — huge ecosystem (VS Code, Discord, Slack), pure JS, owner has familiarity from other projects. But: ~100–200MB installer, ~300–500MB idle RAM, bundled Chromium per app
- **Tauri v2 (chosen)** — ~5–15MB installer, ~80–150MB idle RAM, uses OS native webview (WKWebView on Mac, WebView2 on Win), Rust shell. Growing ecosystem in 2025, first-class Python sidecar pattern via `external_bin` config
- PyQt / PySide — native Python, no JS/web stack. Would throw away React skills, slower UI dev, fewer charting libs
- Flutter Desktop — beautiful UIs, but Dart learning curve, throws away React skills
- .NET MAUI / Avalonia — not aligned with the Python + JS stack

**Rationale:**
- Live financial charts benefit from a smaller memory/CPU footprint — Tauri's lightness translates to smoother rendering on modest hardware
- Small installer size is a UX win for distribution
- Tauri v2 has a well-supported Python sidecar pattern (external_bin) and strong updater plugin
- Rust exposure is minimal: ~200 LOC of Rust glue for sidecar spawn/kill; UI is React, business logic is Python

**Consequences:**
- Rust toolchain (`rustup`, `cargo`) required for dev
- Tauri v2 differs from v1 on plugins and APIs — always reference v2 docs
- OS-native webviews differ slightly (Mac WebKit vs Win WebView2 based on Chromium) — must test both platforms before release
- Supersedes DEC-002 with respect to hosting (React on Vercel → React inside Tauri webview)

---

### DEC-009 | Backend: FastAPI for Python Sidecar
**Date:** 2026-04-21
**Context:**
With the desktop pivot, the backend role changed: a localhost-only sidecar owned by the app, not a public-facing server. Django's strengths (admin UI, DRF, auth) lose their value.

**Options considered:**
- Keep Django for continuity — admin panel was handy during dev. But: admin is less useful in a desktop context (Settings UI replaces it), DRF ceremony unnecessary for localhost-only API, sync ORM fights APScheduler's threading model
- **FastAPI (chosen)** — async-native, minimal boilerplate, first-class Pydantic validation, sub-second startup, good fit for a sidecar
- Flask — simpler than Django but no async, no Pydantic. Outcompeted by FastAPI in this niche
- No framework, raw uvicorn + routes — too minimalist; Pydantic validation is worth the dependency

**Rationale:**
- Fast startup matters — user opens app, sidecar must be ready in < 1s
- Pydantic auto-generates request/response schemas, catches bad data at the boundary
- Async fits the I/O-bound workload (all external API calls)
- No admin needed — the UI IS the admin
- Small dependency footprint → smaller PyInstaller bundle

**Consequences:**
- Supersedes DEC-001 (Django)
- SQLAlchemy 2.x chosen over Django ORM (framework-neutral, async-capable)
- Alembic for migrations (replaces Django migrations)
- FastAPI `@app.on_event("startup")` starts APScheduler; `@app.on_event("shutdown")` stops it

---

### DEC-010 | Database: SQLite
**Date:** 2026-04-21
**Context:**
Single-user desktop app on one machine. Previous plan used Postgres on Neon for hosting reasons (free tier). Those reasons no longer apply.

**Options considered:**
- Neon Postgres — requires network, cold-start risk, 0.5GB storage cap, user's data lives with a 3rd party. Disqualifying for a local desktop app
- Local Postgres — overkill; user would need to install a Postgres server. Bad UX for a "download and run" app
- DuckDB — excellent analytics performance, but single-writer model is awkward for a scheduler writing while UI reads
- **SQLite (chosen)** — embedded (no server), ships inside the Python binary, WAL mode handles concurrent reader + writer fine, ACID, full SQL

**Rationale:**
- Zero-install database
- File-based — easy to back up, inspect, migrate, or delete
- Performance is ample for Phase 1 scale (thousands of price points per asset)
- WAL mode enables concurrent reads during scheduler writes
- User fully owns their data (it's a file on their disk)

**Consequences:**
- Supersedes DEC-005 (standard Postgres indexes on Neon)
- No TimescaleDB — unnecessary at this scale; composite index on `(asset_id, timestamp DESC)` suffices
- Full historical retention — no 90-day pruning needed (local disk is cheap)
- Mandatory pragmas on every connection: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`
- Numeric price fields use `Numeric(18,6)` — SQLite stores these as strings; SQLAlchemy handles the conversion transparently

---

### DEC-011 | Scheduler: APScheduler (in-process)
**Date:** 2026-04-21
**Context:**
Scheduled ingestion jobs (5–15 min intervals) are the core background workload. Previous plan used Celery + Redis on Render. Both irrelevant to a local desktop app.

**Options considered:**
- Celery + Redis — requires a broker process; no reason to run one locally for a single-user app
- Python `schedule` library — too simple, no persistence, no misfire handling
- OS cron / launchd / Task Scheduler — external to the app, requires install-time registration, handles "user closed laptop for 2 hours" poorly
- **APScheduler (chosen)** — pure Python, in-process, `BackgroundScheduler` runs in its own thread inside the sidecar, `SQLAlchemyJobStore` persists schedules to the same SQLite file

**Rationale:**
- No broker process to manage
- Jobs persist across app restarts (job state in SQLite)
- Misfire grace period handles laptop sleep/wake correctly — skip stale runs rather than queueing backlog
- Threaded executor is sufficient for I/O-bound ingestion work
- No extra dependency beyond `APScheduler` + SQLAlchemy (already present)

**Consequences:**
- Supersedes DEC-004 (Celery + django-celery-beat)
- Scheduler starts on sidecar boot, stops on sidecar shutdown
- `misfire_grace_time=60` — runs fired more than 60s late are skipped
- If user keeps app open, ingestion runs continuously — acceptable for personal use
- If user closes app, no ingestion happens — acceptable (desktop app, not a server)
- First-boot empty state is expected: user sees "fetching data…" for a few minutes until the first ingest completes

---

### DEC-012 | No Authentication in Phase 1
**Date:** 2026-04-21
**Context:**
Original plan Sprint 2 included JWT auth, user accounts, registration. For a single-user local desktop app, auth is negative value — adds friction (login to view own data), adds code, adds failure modes.

**Options considered:**
- JWT auth with a local user table — provides structure for future multi-user, but currently pointless
- OS keychain-backed passphrase — adds security but only protects against a party who already has shell access to the user's account (at which point they can read the SQLite file anyway)
- **No auth (chosen)** — app is single-user, data is on the user's disk, access control is delegated to the OS user account

**Rationale:**
- Desktop apps owned by one user don't need their own login screen
- Sidecar binds to 127.0.0.1 — not network-reachable from other machines
- SQLite file lives in user's private app-data dir — OS file permissions are the access control
- Simpler = fewer bugs = ship faster

**Consequences:**
- Old Sprint 2 ("Auth, Watchlists & Price Alerts") collapsed into new Sprint 4 without the auth parts
- Watchlists are simple local tables — no `user_id` foreign key
- Alerts are desktop notifications (via Tauri plugin), not emails
- If multi-device sync is ever added, a proper account system will be built then — not now
- Input validation still required on every endpoint (via Pydantic) — defend against bugs, not attackers

---

### DEC-013 | Target Platforms: macOS + Windows (Linux deferred)
**Date:** 2026-04-21
**Context:**
Owner uses Mac and Windows. Linux is a possibility but not a priority for Phase 1.

**Options considered:**
- All three from day one — adds CI complexity, testing surface, distribution formats (.AppImage, .deb, .rpm) for no immediate user benefit
- **Mac + Windows only for Phase 1 (chosen)** — ship faster, add Linux after Phase 1 if demand exists or the owner adopts a Linux workstation

**Rationale:**
- Code is cross-platform by construction (Python + React + Tauri) — Linux support will be near-free to add later
- Sprint 5 savings: GitHub Actions matrix stays `{macos-latest, windows-latest}`, no AppImage/deb/rpm tooling, no additional platform-specific bug hunts
- Code signing: Mac (Apple Developer, $99/yr) + Windows (EV cert or Azure Trusted Signing) are already two sign-code pipelines — adding Linux as a third is real work

**Consequences:**
- CI matrix: `{macos-latest, windows-latest}` in Phase 1
- Sprint 5 builds `.dmg` + `.msi` only
- Linux listed as a post-Phase-1 backlog item (see PROGRESS.md)
- All code paths remain platform-neutral (no Win-only or Mac-only branches unless absolutely required) — adding Linux stays cheap later

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
