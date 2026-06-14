# ARCHITECTURE.md — FinTrack Deep Architecture Reference
# =========================================================
# NOT auto-loaded. Import on demand: @.claude/ARCHITECTURE.md
# Reference when: designing services, changing DB schema, adding ingestion sources.

---

## System Overview

FinTrack is a **single-user, cross-platform desktop application** for tracking market
data (stocks, ETFs, commodities, crypto), aggregating financial news, and (in Phase 2)
running local ML-based price forecasting and news sentiment analysis.

Everything runs on the user's machine. Data is stored locally in SQLite. There are
no cloud services, no remote servers, no authentication, and no telemetry. The only
network calls are outbound to free public data sources.

Target platforms for Phase 1: macOS and Windows. Linux deferred to post-Phase-1.

---

## Top-Level Architecture

```
┌──────────────────────────────────────────────────────────┐
│  FinTrack.app  (single bundled installer per OS)         │
│                                                          │
│  ┌───────────────────────────────────────────────────┐   │
│  │  Tauri v2 shell (Rust, ~200 LOC)                  │   │
│  │    - Spawns sidecar process on startup            │   │
│  │    - Picks random free port, passes via env var   │   │
│  │    - Waits for /api/health/ before showing window │   │
│  │    - Exposes `get_sidecar_port()` Tauri command   │   │
│  │    - Kills sidecar cleanly on app exit            │   │
│  │    - Desktop notifications (Tauri plugin)         │   │
│  │                                                   │   │
│  │    ┌──────────────────────────────────────────┐   │   │
│  │    │  React 18 UI (Vite build, inside webview)│   │   │
│  │    │    - fetch() → http://127.0.0.1:<port>   │   │   │
│  │    │    - Zustand global state                │   │   │
│  │    │    - TradingView Lightweight Charts      │   │   │
│  │    │    - Tailwind CSS                        │   │   │
│  │    └──────────────────────────────────────────┘   │   │
│  └───────────────────────────────────────────────────┘   │
│                           ↑ HTTP (localhost only)         │
│  ┌────────────────────────┴──────────────────────────┐   │
│  │  Python sidecar (FastAPI + uvicorn)               │   │
│  │    - Listens on 127.0.0.1:<random free port>      │   │
│  │    - APScheduler in-process (no broker)           │   │
│  │    - SQLAlchemy 2.x → SQLite (WAL mode)           │   │
│  │    - Ingestion: yfinance, CoinGecko, FRED, RSS    │   │
│  └────────────────────────┬──────────────────────────┘   │
│                           ↓                              │
│  ┌────────────────────────┴──────────────────────────┐   │
│  │  SQLite file in OS app-data dir                   │   │
│  │   Mac: ~/Library/Application Support/FinTrack/    │   │
│  │   Win: %APPDATA%\FinTrack\                        │   │
│  └───────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

---

## Components

### Tauri v2 Shell

- **Language:** Rust (minimal — config + sidecar process management)
- **Location:** `shell/src-tauri/`
- **Responsibilities:**
  - Open app window, render webview, load React bundle
  - Pick a random free port at startup (`TcpListener::bind(("127.0.0.1", 0))` → read assigned port → drop)
  - Spawn the Python sidecar as a child process with `FINTRACK_PORT` env var
  - Poll `/api/health/` with exponential backoff (max 10s) before showing the window
  - Expose `get_sidecar_port()` Tauri command so the frontend can form API URLs
  - Kill the sidecar cleanly on app exit (signal on Mac/Linux, `TerminateProcess` on Win)
  - Register Tauri plugins: `notification` (alerts, Sprint 4), `updater` (Sprint 5)
- **Bundled installers:** `.dmg` for Mac, `.msi` for Windows

### React UI

- **Framework:** React 18 + TypeScript + Vite
- **Location:** `shell/src/`
- **Styling:** Tailwind CSS
- **State:** Zustand — one store per domain (`marketStore`, `newsStore`, `watchlistStore`, `settingsStore`)
- **Charts:** TradingView Lightweight Charts (MIT, small bundle, performant)
- **API client:** `shell/src/api/client.ts` — thin `fetch` wrapper that:
  1. Calls `invoke<number>('get_sidecar_port')` on app boot
  2. Caches the base URL `http://127.0.0.1:${port}`
  3. Exposes `get`, `post`, `put`, `delete` helpers with `AbortController` support
- **Build:** Vite outputs to `shell/dist/`, embedded by Tauri bundler

### Python Sidecar

- **Framework:** FastAPI + uvicorn
- **Location:** `sidecar/`
- **Entry point:** `sidecar/main.py` — reads `FINTRACK_PORT` env var, starts uvicorn on 127.0.0.1:port
- **Binding:** 127.0.0.1 only (never 0.0.0.0) — not reachable off the local machine
- **No authentication:** local-only, same-origin by process design. Still validate all inputs with Pydantic
- **Layout:**
  ```
  sidecar/
  ├── main.py                       ← uvicorn entrypoint
  ├── config.py                     ← loads env vars, resolves DB path via platformdirs
  ├── api/                          ← FastAPI route modules
  │   ├── __init__.py               ← registers routers on the FastAPI app
  │   ├── health.py
  │   ├── assets.py
  │   ├── prices.py
  │   ├── news.py
  │   ├── watchlist.py
  │   └── alerts.py
  ├── db/
  │   ├── engine.py                 ← async engine + session factory, pragmas
  │   ├── models.py                 ← SQLAlchemy ORM models
  │   └── migrations/               ← Alembic migration scripts
  ├── scheduler/
  │   ├── __init__.py               ← creates BackgroundScheduler, starts on app startup
  │   └── jobs.py                   ← ingest_prices, ingest_crypto, ingest_news, check_alerts
  └── ingestion/
      ├── yfinance_fetcher.py
      ├── coingecko_fetcher.py
      ├── fred_fetcher.py
      └── rss_fetcher.py
  ```

### APScheduler

- **Type:** `BackgroundScheduler` — runs in its own thread inside the sidecar
- **Jobstore:** `SQLAlchemyJobStore` pointing at the same SQLite file → jobs persist across restarts
- **Executor:** `ThreadPoolExecutor(max_workers=4)` — ingestion is I/O-bound, threads are fine
- **Misfire grace:** 60s — on laptop sleep/wake, skip stale runs instead of queueing a backlog
- **Lifecycle:** started in FastAPI `@app.on_event("startup")`, stopped in `@app.on_event("shutdown")`
- **Jobs (Phase 1):**
  - `ingest_prices` — every 5 min, batched yfinance `yf.download` for all active stock/ETF assets
  - `ingest_crypto` — every 5 min, CoinGecko top 10
  - `ingest_news` — every 15 min, Yahoo RSS for each watchlisted symbol (Sprint 4)
  - `ingest_macro` — daily at 06:00 local, FRED indicators
  - `check_price_alerts` — every 5 min (Sprint 4)
  - `vacuum_db` — weekly, `VACUUM` + `ANALYZE`

### SQLite + SQLAlchemy

- **Engine:** SQLAlchemy 2.x, **synchronous** throughout. The API endpoints are sync (`def`, not `async def`) and FastAPI runs them in its anyio threadpool; scheduler jobs run in APScheduler's `ThreadPoolExecutor`. Both share **one** module-level engine + sessionmaker (`sidecar/db/engine.py`, lazily built under a lock) — not separate factories. WAL + `busy_timeout=5000` make the concurrent threadpool-reader / scheduler-writer access safe; the APScheduler `SQLAlchemyJobStore` reuses this same engine so it inherits the pragmas. There is no async DB path; an earlier draft of this doc described one that was never built.
- **File path (resolved via `platformdirs`):**
  - Dev: `./fintrack.db` in repo root (gitignored)
  - Prod Mac: `~/Library/Application Support/FinTrack/fintrack.db`
  - Prod Win: `%APPDATA%\FinTrack\fintrack.db`
- **Pragmas on every connection** (via SQLAlchemy `event.listens_for(engine, "connect")`):
  - `journal_mode=WAL` — concurrent readers + scheduler writer
  - `synchronous=NORMAL` — durable enough for single-user desktop
  - `foreign_keys=ON`
  - `busy_timeout=5000` — 5s wait before surfacing SQLITE_BUSY
- **Migrations:** Alembic. Run automatically on sidecar startup — if DB version < head, migrate before accepting requests

---

## Data Model (Phase 1)

```python
# sidecar/db/models.py (sketch)

class Asset(Base):
    id         = Column(Integer, primary_key=True)
    symbol     = Column(String, unique=True, index=True)     # "AAPL", "BTC-USD"
    name       = Column(String)
    asset_type = Column(Enum("stock","etf","crypto","commodity","index"))
    exchange   = Column(String, nullable=True)
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

class PricePoint(Base):
    id        = Column(Integer, primary_key=True)
    asset_id  = Column(ForeignKey("assets.id"), index=True)
    timestamp = Column(DateTime, index=True)
    open      = Column(Numeric(18,6))
    high      = Column(Numeric(18,6))
    low       = Column(Numeric(18,6))
    close     = Column(Numeric(18,6))
    volume    = Column(BigInteger)
    __table_args__ = (
        Index("ix_pricepoint_asset_ts", "asset_id", "timestamp"),
        UniqueConstraint("asset_id", "timestamp"),
    )

class Article(Base):
    id             = Column(Integer, primary_key=True)
    url            = Column(String, unique=True)    # dedup key
    headline       = Column(String)
    source         = Column(String)
    published_at   = Column(DateTime, index=True)
    sentiment      = Column(Float, nullable=True)   # Phase 2 populates
    related_assets = relationship("Asset", secondary=article_asset_table)

class Watchlist(Base):
    id    = Column(Integer, primary_key=True)
    name  = Column(String)                          # user may create multiple
    items = relationship("WatchlistItem", order_by="WatchlistItem.position")

class WatchlistItem(Base):
    id           = Column(Integer, primary_key=True)
    watchlist_id = Column(ForeignKey("watchlists.id"))
    asset_id     = Column(ForeignKey("assets.id"))
    position     = Column(Integer)                  # for drag-reorder

class PriceAlert(Base):
    id           = Column(Integer, primary_key=True)
    asset_id     = Column(ForeignKey("assets.id"))
    threshold    = Column(Numeric(18,6))
    direction    = Column(Enum("above","below"))
    triggered    = Column(Boolean, default=False)
    triggered_at = Column(DateTime, nullable=True)
```

---

## Data Flow

```
yfinance / CoinGecko / FRED / Yahoo RSS
           ↓  (APScheduler jobs, 5–15 min cadence)
sidecar/ingestion/*_fetcher.py  →  normalisation  →  bulk upsert
           ↓
SQLAlchemy ORM → SQLite (WAL mode)
           ↓
FastAPI endpoints  (/api/prices/..., /api/news/..., /api/watchlist/..., /api/alerts/...)
           ↓  HTTP (127.0.0.1:<port>)
React UI in Tauri webview  →  Zustand stores  →  Lightweight Charts
           ↓
User sees live data
```

---

## Cross-Platform Considerations

| Concern | Mac | Windows | Linux (deferred) |
|---------|-----|---------|------------------|
| App-data dir | `~/Library/Application Support/FinTrack/` | `%APPDATA%\FinTrack\` | `~/.local/share/fintrack/` |
| Installer | `.dmg` (Tauri bundler) | `.msi` (WiX via Tauri) | `.AppImage` + `.deb` |
| Code signing | Apple Developer ID + notarisation required | EV cert OR Azure Trusted Signing | Not typically required |
| Python bundling | PyInstaller → Mach-O inside `.app` | PyInstaller → `.exe` inside Tauri bundle | PyInstaller → ELF |
| Sidecar spawn | `Command::new("fintrack-sidecar")` from `resources/` | Same, `.exe` extension in prod | Same |
| Native notifications | `osascript`-backed (Tauri plugin) | Windows Action Center (Tauri plugin) | libnotify (Tauri plugin) |

All path handling uses `pathlib.Path` (Python) and Tauri `path` API (Rust) — no hardcoded separators anywhere.

---

## Phase 2 — Local ML (future — do not start in Phase 1)

- **Sentiment:** VADER — lightweight, CPU-only, lives inside sidecar, writes to `Article.sentiment`
- **Price forecasting:** Prophet or small LSTM, trained locally on user's `PricePoint` history
- **Inference:** Models exported to ONNX, loaded in sidecar via `onnxruntime`
- **Training pipeline:** `ml/train.py` runnable on demand from Settings UI — user controls when retraining happens
- **Dependencies isolated:** `requirements-ml.txt` installed only when Phase 2 begins
- **No cloud inference:** everything runs on-device. User's data never leaves their machine

---

## What's Explicitly NOT in the Architecture

The following were in the original web-app plan (see DECISIONS.md DEC-001–DEC-006) and
are **deliberately excluded** under the desktop architecture:

- Django, Django REST Framework, Django admin
- Celery, Redis, any message broker
- PostgreSQL, Neon, any cloud database
- Render.com, Vercel, Upstash, any hosting provider
- TimescaleDB
- JWT authentication, OAuth, any auth system (single-user local)
- Rate limiting (sidecar binds to 127.0.0.1; single-user, no abuse vector)
- Wildcard CORS — CORS middleware *is* enabled, but with a fixed allowlist covering only the dev Vite origin (`http://localhost:1420`) and the prod webview origins (`tauri://localhost`, `https://tauri.localhost`). Required because the webview is cross-origin to the sidecar
- Docker Compose for services (no services to compose)
- Kubernetes, Terraform, nginx
- cron-job.org ping (no sleeping service to keep alive)
- Email alerts via SMTP (replaced by desktop notifications)
- TensorFlow, PyTorch in Phase 1 (Phase 2 uses ONNX for inference only)
- MLflow, Weights & Biases, Feast (heavy cloud-ops tools not needed for single-user local ML)

If any of these resurface in a suggestion or memory recall, check this list first and
challenge before acting.

---

## Environment Variables Reference

```bash
# Injected by Tauri shell into sidecar at runtime
FINTRACK_PORT=54321                 # random free port picked by shell at startup

# Dev / runtime overrides
FINTRACK_DB_PATH=./fintrack.db      # override DB path (tests use this)
FINTRACK_EXTERNAL_SIDECAR=1         # dev only — shell won't spawn; use port 8765
LOG_LEVEL=INFO                      # DEBUG in dev

# External data sources
FRED_API_KEY=                       # free key from fred.stlouisfed.org
```

No `.env` ships with the app. User-configurable runtime settings (refresh intervals,
which data sources are enabled, etc.) live in the SQLite DB and are edited via the
Settings UI (Sprint 3).
