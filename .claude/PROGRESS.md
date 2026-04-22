# PROGRESS.md — Session & Task Tracker
# FinTrack — Market Intelligence Desktop App
# ============================================
#
# READING INSTRUCTIONS (for Claude):
#   1. Read CURRENT STATE — this is the only section you act on immediately.
#   2. Consult SPRINT BACKLOG for the full task list.
#   3. SESSION LOG is historical — read only to understand prior decisions.

---

## ⚡ CURRENT STATE
> Rewritten at the end of every session. Single source of truth for RIGHT NOW.

**Last updated:** 2026-04-22 — Session 002 (checkpoint 3 — Sprint 1 kill-test closed via ctrlc + parent-watchdog)
**Active sprint:** Sprint 2 — Market Data Pipeline (first pass landed; follow-ups optional)
**Overall status:** 🟢 Sprint 1 complete; Sprint 2 backend pipeline landed; sidecar shutdown verified under SIGTERM and SIGKILL

### What was just completed (Sprint 2 first pass)
- **PricePoint model + migration**: `sidecar/db/models.py` — `PricePoint` with FK to `assets`, `Numeric(18,6)` for o/h/l/c, `BigInteger` volume, composite index `ix_price_points_asset_ts` on `(asset_id, timestamp)`, unique constraint `uq_price_points_asset_ts` for dedup. Alembic migration `0002_create_price_points.py` — runs cleanly on top of 0001. Asset ↔ PricePoint relationship wired with `cascade="all, delete-orphan"`
- **Seed script**: `sidecar/db/seed.py` with `DEFAULT_ASSETS` tuple (AAPL, MSFT, GOOGL, NVDA, SPY, QQQ, GLD, BTC-USD, ETH-USD, SOL-USD), idempotent `seed_default_assets()` that checks existing symbols before insert. Called from lifespan when `FINTRACK_ENABLE_SEED=true`
- **yfinance fetcher**: `sidecar/ingestion/yfinance_fetcher.py` — batched `yf.download(tickers=..., group_by="ticker", auto_adjust=True, threads=True)`, exponential backoff with jitter (up to 4 attempts, base 1s, cap 30s), `PriceBar` dataclass output, NaN/None-safe normalization, UTC-aware timestamps. Handles single-symbol vs multi-symbol DataFrame shapes
- **Scheduler**: `sidecar/scheduler/__init__.py` — `BackgroundScheduler` + `SQLAlchemyJobStore` pointing at the SQLite DB, `ThreadPoolExecutor(max_workers=4)`, `misfire_grace_time=60`, `coalesce=True`, `max_instances=1`, UTC timezone. `start()`/`shutdown()` idempotent with module-level lock. Gated by `FINTRACK_ENABLE_SCHEDULER` (default true; disabled in tests)
- **ingest_prices job**: `sidecar/scheduler/jobs.py` — loads active asset symbols, calls `fetch_prices`, maps back to asset_ids, bulk-upserts via `sqlite.insert(...).on_conflict_do_nothing(index_elements=["asset_id","timestamp"])`. Returns new-row count. FetcherError is caught and logged (scheduler retries on next tick)
- **API endpoints**: `GET /api/assets/?active_only=true` (default), `GET /api/prices/{symbol}/?from=&to=&limit=500` (alpha-case-insensitive, 404 on unknown, ascending time, limit 1–10000). Pydantic response models with `ConfigDict(from_attributes=True)`, Annotated query params to satisfy B008
- **Lifespan wiring**: migrations → seed (if enabled) → scheduler start (if enabled); shutdown drains scheduler
- **Live verification**: one-shot `ingest_prices()` against Yahoo → 714 bars pulled for the 10 seed assets, `/api/prices/AAPL/?limit=3` returned 5-minute OHLCV bars as expected. Unique constraint prevents duplicate inserts on re-run
- Verifications: `pytest` 21/21 green, `ruff check .` clean, `mypy --strict sidecar/` clean on 20 files

### What was deferred from Sprint 2 (next pass)
- **`ingestion/coingecko_fetcher.py`** — Yahoo already covers crypto via `BTC-USD`/`ETH-USD`/`SOL-USD`, so a dedicated CoinGecko path is a fallback/complement, not critical path
- **`ingestion/fred_fetcher.py` + `ingest_macro` job** — needs `FRED_API_KEY`; gated until the user provisions one
- **`ingest_crypto` job** — same reasoning as CoinGecko; current `ingest_prices` already ingests crypto symbols alongside stocks
- **`vacuum_db` weekly job** — low priority, add with Sprint 4 scheduler work

### What to work on NEXT (in order)
1. [x] ~~Commit Sprint 2 first pass~~ — landed as `bd26a2f`
2. [x] ~~Finish Sprint 1 kill-test~~ — ctrlc handler + parent-PID watchdog verified on Mac; commit pending
3. [ ] **Commit Sprint 1 kill-test fix** — `fix(sprint-1): clean sidecar shutdown via ctrlc + parent-pid watchdog`
4. [ ] **Sprint 2 follow-up** (optional before Sprint 3): CoinGecko fetcher + `ingest_crypto`, FRED fetcher + `ingest_macro`, `vacuum_db` weekly
5. [ ] **Start Sprint 3 — React UI Dashboard**: Tailwind + Zustand, app shell, watchlist grid, asset detail with TradingView Lightweight Charts

### Active blockers
- None

### Session notes (additions from Sprint 2)
- **yfinance DataFrame shape**: with a single symbol, `yf.download` returns a flat-column DataFrame (`Open, High, Low, Close, Volume`); with multiple symbols and `group_by="ticker"`, columns become a MultiIndex `(symbol, field)`. Fetcher branches on `len(unique) == 1`
- **SQLite on_conflict_do_nothing**: pass `index_elements=["asset_id","timestamp"]` (column names), not the constraint name. Works with either a unique index or a named unique constraint
- **`CursorResult.rowcount` typing**: `session.execute(insert_stmt)` returns `Result[Any]` in SQLAlchemy's type stubs; actual returned object is `CursorResult` which exposes `rowcount`. Use `cast(CursorResult[...], session.execute(stmt))` to satisfy mypy
- **FastAPI B008**: ruff flags `= Query(...)` in defaults. Use `Annotated[T, Query(alias="from")] = None` — also the idiomatic FastAPI 0.110+ style
- **Test isolation**: conftest sets `FINTRACK_ENABLE_SCHEDULER=false` and `FINTRACK_ENABLE_SEED=false` at module import (before `sidecar.config` is loaded) so `TestClient(app)` lifespan doesn't start a live scheduler or seed real data. `isolated_db` fixture points `FINTRACK_DB_PATH` at a tmp file and resets the global engine/sessionmaker per test
- **URL-encoded `+` in query strings**: `datetime.isoformat()` emits `+00:00` which `TestClient.get(url, ...)` does NOT auto-encode when you interpolate it into the URL. Use `params={...}` so httpx encodes it, otherwise FastAPI returns 422
- **SQLite DateTime without tz**: stored values come back as naive datetimes even though we write UTC-aware. Pydantic then serialises without offset. Frontend must treat timestamps as UTC. Acceptable for now; revisit if confusion arises
- **CORS required even on localhost** (from Sprint 1): Vite `http://localhost:1420` ≠ sidecar `http://127.0.0.1:<port>`. Keep the 4-origin allowlist explicit
- **macOS window-close quirk** (from Sprint 1): `WindowEvent::CloseRequested` → `app.exit(0)` handler is required so `RunEvent::Exit` fires and the python child is killed
- **Tauri `RunEvent::Exit` does NOT fire on OS signals**: SIGTERM / SIGINT to the Tauri process kills the shell without invoking the `run(|h, event|)` callback, so the python sidecar is orphaned. Fix: install a `ctrlc::set_handler` in `setup()` that calls `app_handle.exit(0)` — that's what routes the signal through the tauri event loop and fires `RunEvent::Exit → child.kill()`
- **Parent-PID watchdog as safety net**: SIGKILL bypasses `ctrlc`, and any crash (OOM, abort, etc.) also skips cleanup. `sidecar/main.py` starts a daemon thread that polls `os.getppid()` every 2s; when the ppid changes (orphan → reparented to launchd/init), it calls `os._exit(0)`. Disabled by `FINTRACK_DISABLE_PARENT_WATCHDOG=1`, and skipped when initial ppid is 1 (already a top-level process)
- **Dev-mode osascript limitation**: `pnpm tauri dev` produces an unbundled `target/debug/shell` binary that has `CFBundleIdentifier=NULL` and `LSDisplayName="shell"`, so `osascript -e 'tell application "FinTrack" to quit'` cannot target it. GUI-close verification must wait for bundled `.app` (Sprint 5) or be done manually by the user
- **Dev DB path heuristic**: when CWD has `pyproject.toml` + `sidecar/`, DB is `./fintrack.db` (gitignored); otherwise `platformdirs.user_data_dir("FinTrack","FinTrack")`
- **Tauri v2 template split**: entry is `src-tauri/src/lib.rs`, not `main.rs`
- **ESLint flat config gotcha**: use `reactHooks.configs.flat["recommended-latest"]`, and `pnpm -C <dir>` (not `--prefix` which is npm-only)
- Phase 1 dev runs unsigned binaries — code signing deferred to Sprint 5
- SQLite WAL mode mandatory — scheduler writes concurrently with UI reads
- Sidecar never binds to 0.0.0.0 — always 127.0.0.1

---

## 📋 SPRINT BACKLOG

---

### Sprint 1 — Desktop Scaffold
**Goal:** Tauri window opens, FastAPI sidecar starts, SQLite initialised, health check round-trips end-to-end
**Scope:** Phase 1 start

#### Milestone 1A — Tauri Shell
- [x] `shell/` initialised with Tauri v2 (React + TS + Vite template)
- [x] Tauri project identifier `com.fintrack.app`, display name "FinTrack"
- [x] `pnpm tauri dev` launches a blank window on Mac
- [x] Tauri v2 config: window size 1280×800, devtools in dev only, CSP allows `connect-src http://127.0.0.1:*`

#### Milestone 1B — Python Sidecar Skeleton
- [x] `sidecar/` created with FastAPI app scaffold
- [x] `GET /api/health/` returns `{"status": "ok", "version": "0.1.0"}`
- [x] `requirements.txt` pinned: `fastapi`, `uvicorn[standard]`, `SQLAlchemy>=2`, `alembic`, `APScheduler`, `pydantic>=2`, `pydantic-settings`, `python-dotenv`, `platformdirs`, `yfinance`, `requests`, `feedparser`
- [x] `requirements-dev.txt`: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`, `types-requests`
- [x] Standalone run: `python -m sidecar.main` → server listens on `FINTRACK_PORT` → `curl` to `/api/health/` returns JSON

#### Milestone 1C — SQLite + SQLAlchemy + Alembic
- [x] `sidecar/db/engine.py` — SQLAlchemy engine against SQLite with WAL / synchronous=NORMAL / foreign_keys=ON / busy_timeout=5000 pragmas (installed via `event.listens_for(engine,"connect")`)
- [x] Path resolution helper: dev → `./fintrack.db` when repo root detected; prod → OS app-data dir via `platformdirs`
- [x] Alembic initialised in `sidecar/db/migrations/` + `alembic.ini` + programmatic runner in `db/migrations_runner.py`
- [x] First migration `0001_create_assets` creates `assets` table (id, symbol, name, asset_type, is_active, created_at) with unique index `ix_assets_symbol`
- [x] Auto-run Alembic to head on sidecar startup via FastAPI `lifespan`

#### Milestone 1D — Tauri ↔ Sidecar Wiring
- [x] `src-tauri/src/lib.rs` picks a random free port via `TcpListener::bind("127.0.0.1:0")` (Tauri v2 template uses `lib.rs`, not `main.rs`)
- [x] Spawns sidecar as child process (`.venv/bin/python -m sidecar.main`) with `FINTRACK_PORT` env var set
- [x] Waits for `/api/health/` to return 200 before proceeding (up to 10s, 200ms poll interval via `ureq`)
- [x] Kills sidecar cleanly on app exit — `RunEvent::Exit` → `child.kill()` + `child.wait()`; `WindowEvent::CloseRequested` → `app.exit(0)` so the Exit event actually fires on macOS
- [x] `get_sidecar_port` Tauri command returns the chosen port from shared `SidecarState`
- [x] Dev escape hatch: `FINTRACK_EXTERNAL_SIDECAR=1` skips spawning and uses port `8765`

#### Milestone 1E — End-to-end Health Check
- [x] React UI calls `invoke('get_sidecar_port')`, then `fetch('http://127.0.0.1:${port}/api/health/')`
- [x] Response rendered as a status badge (green ✓ healthy, red ✗ error with retry button); polls every 2s with `AbortController` cleanup
- [x] Verified on Mac: `pnpm tauri dev` → window opens → badge shows ✓ healthy
- [x] Verified on Mac: SIGTERM to `target/debug/shell` → both shell and sidecar gone within 1s (ctrlc handler → `app.exit(0)` → `RunEvent::Exit` → `child.kill()`)
- [x] Verified on Mac: SIGKILL to `target/debug/shell` → sidecar gone within 2s via parent-PID watchdog (ctrlc cannot catch SIGKILL, so this proves the fallback path)
- [x] SQLite file created in expected location on first run (verify via `sqlite3 fintrack.db .schema`)

#### Sprint 1 verification checklist
- [x] `pytest` — all tests pass (2/2)
- [x] `ruff check .` — 0 errors
- [x] `mypy sidecar/` — 0 errors (strict mode)
- [x] `pnpm -C shell lint` — 0 errors (ESLint 10 + typescript-eslint + react-hooks flat config)
- [x] `pnpm tauri dev` on Mac: window opens with ✓ status badge
- [x] SQLite file `./fintrack.db` contains `assets` table after first run

---

### Sprint 2 — Market Data Pipeline
**Goal:** Live price data from yfinance + CoinGecko into SQLite on a schedule, queryable via sidecar API
**Scope:** Phase 1

#### Tasks (refine at Sprint 2 start)
- [x] `sidecar/db/models.py` — `Asset`, `PricePoint` SQLAlchemy models (composite index on `asset_id, timestamp`)
- [x] Alembic migration for `price_points`
- [x] `sidecar/ingestion/yfinance_fetcher.py` — batched `yf.download`, exponential backoff
- [ ] `sidecar/ingestion/coingecko_fetcher.py` — top 10 crypto OHLCV (deferred — Yahoo covers crypto for now)
- [ ] `sidecar/ingestion/fred_fetcher.py` — macro indicators (deferred — needs `FRED_API_KEY`)
- [x] `sidecar/scheduler/__init__.py` — APScheduler `BackgroundScheduler` + `SQLAlchemyJobStore`, misfire grace 60s
- [x] `sidecar/scheduler/jobs.py` — `ingest_prices` (5 min covering stocks/ETFs/crypto via yfinance)
- [ ] `sidecar/scheduler/jobs.py` — `ingest_crypto` (5 min via CoinGecko — deferred)
- [ ] `sidecar/scheduler/jobs.py` — `ingest_macro` (daily 06:00 via FRED — deferred)
- [x] `GET /api/assets/` — list tracked assets
- [x] `GET /api/prices/{symbol}/?from=&to=&limit=` — last N price points with date filter
- [x] Seed script: 10 default assets (AAPL, MSFT, GOOGL, NVDA, SPY, QQQ, GLD, BTC-USD, ETH-USD, SOL-USD)

#### Sprint 2 verification checklist
- [x] `pytest` — 21/21 tests pass (migrations, seed, API assets/prices, yfinance fetcher normalization, ingest_prices job upsert/idempotency)
- [x] `ruff check .` — 0 errors
- [x] `mypy --strict sidecar/` — 0 errors on 20 files
- [x] Live yfinance ingest verified: `ingest_prices()` → 714 bars across 10 seed assets
- [x] Live API verified: `/api/assets/` returns seeded list, `/api/prices/AAPL/?limit=3` returns 5-min OHLCV bars, `/api/prices/ZZZ/` returns 404

---

### Sprint 3 — React UI Dashboard
**Goal:** Interactive dashboard with live prices, charts, and asset detail pages
**Scope:** Phase 1

#### Tasks (refine at Sprint 3 start)
- [ ] Tailwind + Zustand set up in `shell/src/`
- [ ] App shell: sidebar nav, header, dark mode toggle (respects OS preference)
- [ ] Dashboard page: watchlist grid with sparkline + day change %
- [ ] Asset detail page: full OHLCV chart (TradingView Lightweight Charts), recent news sidebar
- [ ] Market overview: top movers, sector heatmap
- [ ] Settings page: data source toggles, refresh intervals, clear cache, DB file path

---

### Sprint 4 — News, Watchlists & Desktop Alerts
**Goal:** News aggregation, local watchlists, desktop-native price alerts
**Scope:** Phase 1

#### Tasks (refine at Sprint 4 start)
- [ ] `Article` model + `sidecar/ingestion/rss_fetcher.py` (Yahoo Finance RSS via `feedparser`)
- [ ] `ingest_news` scheduler job (15-min interval per watchlisted symbol)
- [ ] `GET /api/news/`, `GET /api/news/?symbol=AAPL`
- [ ] `Watchlist` + `WatchlistItem` models (local tables, no user_id — single user)
- [ ] Watchlist CRUD endpoints + UI (add/remove/reorder via drag)
- [ ] `PriceAlert` model (asset_id, threshold, direction, triggered flag, triggered_at)
- [ ] `check_price_alerts` scheduler job (5-min interval)
- [ ] Desktop notifications via Tauri `notification` plugin when alert triggers
- [ ] Alert history page in UI

---

### Sprint 5 — Packaging & Distribution
**Goal:** Signed installers for Mac and Windows, auto-updater, release pipeline
**Scope:** Phase 1 close

#### Tasks (refine at Sprint 5 start)
- [ ] Python sidecar frozen with PyInstaller (one-folder mode) — verify SQLite + yfinance + APScheduler work in frozen bundle
- [ ] Tauri config bundles frozen sidecar as `external_bin`
- [ ] GitHub Actions matrix: `macos-latest` + `windows-latest`
- [ ] Mac code signing + notarisation (Apple Developer ID)
- [ ] Windows signing (EV cert OR Azure Trusted Signing)
- [ ] Tauri updater plugin configured (GitHub Releases as update feed)
- [ ] First release: v0.1.0 tagged, `.dmg` + `.msi` published to GitHub Releases
- [ ] `docs/development/release_process.md`

---

### Phase 2 — Local ML (future — do not start until Phase 1 complete)
- [ ] Sentiment analysis on article headlines — VADER (lightweight, CPU only)
- [ ] Price forecasting — Prophet or small LSTM, trained locally on user's PricePoint history
- [ ] Models exported to ONNX, loaded in sidecar via `onnxruntime`
- [ ] Training pipeline in `ml/train.py` — user-triggered from Settings UI
- [ ] Only then install `requirements-ml.txt`

---

### Post-Phase-1 — Linux Support
- [ ] Add `ubuntu-latest` to GitHub Actions matrix — build `.AppImage` + `.deb`
- [ ] Verify sidecar runs on Linux (PyInstaller ELF target)
- [ ] Document Linux-specific packaging quirks in `docs/development/`
- [ ] Tauri updater: adjust feed for Linux

---

## 📅 SESSION LOG
> Append a new entry per session. Never edit old entries.

============================================================
SESSION 000 | DATE: 2026-03-06 | Sprint: Pre-Sprint (Setup)
============================================================

### Completed this session
- Initial 6 architectural decisions resolved and logged in DECISIONS.md
- Free hosting stack scoped: Render.com + Neon + Upstash + Vercel
- Claude Code context system initialised

### Key decisions made
- DEC-001 through DEC-006 — see DECISIONS.md
- NOTE: DEC-001, DEC-002, DEC-004, DEC-005 were superseded in Session 001 when the architecture pivoted to desktop

### Next session: start with
1. [Superseded by Session 001 pivot]

============================================================
END SESSION 000
============================================================

============================================================
SESSION 001 | DATE: 2026-04-21 | Sprint: Pre-Sprint (Pivot)
============================================================

### Completed this session
- Comprehensive re-evaluation of project plan (prompted by long pause + owner's preference for desktop form factor matching other current projects)
- Architecture pivoted: web-app → single-user cross-platform desktop app
- Stack locked: Tauri v2 + React + TypeScript + FastAPI sidecar + SQLite + APScheduler
- Target platforms locked: macOS + Windows (Linux deferred to post-Phase-1)
- Cleanup: removed obsolete Django backend, Celery config, Docker Compose infra, Kubernetes/Terraform configs, kitchen-sink ML scaffolds, bloated 218-line requirements.txt
- Archived: `Project Plan FinTrack.txt`, `Key considerations.txt`, `Project folder structure.txt`, `important_dev_notes.txt` → `docs/archive/`
- Rewrote CLAUDE.md, PROGRESS.md, ARCHITECTURE.md
- DECISIONS.md: marked DEC-001, DEC-002, DEC-004, DEC-005 as superseded; appended DEC-007 through DEC-013

### Key decisions made
- DEC-007: Pivot to single-user desktop app
- DEC-008: Tauri v2 over Electron (installer size, RAM, perf)
- DEC-009: FastAPI over Django for sidecar
- DEC-010: SQLite over Postgres (local-only, user owns data)
- DEC-011: APScheduler over Celery + Redis (no broker needed)
- DEC-012: No auth in Phase 1 (local-only, OS access control)
- DEC-013: Mac + Win first; Linux deferred

### Problems encountered & solutions
- Repo on disk contained orphaned Django + kitchen-sink ML scaffolds inconsistent with CLAUDE.md (leftover from a pre-reset attempt) → deleted in full before rewriting docs

### Next session: start with
1. Run `/project:start`
2. Sprint 1, Milestone 1A — Tauri v2 init in `shell/` via `pnpm create tauri-app` (React + TS + Vite template)
3. Then Milestone 1B — FastAPI sidecar skeleton with `/api/health/` endpoint

============================================================
END SESSION 001
============================================================
