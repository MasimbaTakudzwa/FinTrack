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

**Last updated:** 2026-04-22 — Session 003 (checkpoint 6 — Sprint 3 milestones 3A–3E landed)
**Active sprint:** Sprint 3 — React UI Dashboard (3F next — final)
**Overall status:** 🟢 Sprints 1 & 2 complete; Sprint 3 5/6 milestones complete — dashboard, asset detail chart, market overview all live

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

### What was also completed (Sprint 2 close — `afe3170`)
- **CoinGecko fetcher + `ingest_crypto`**: `sidecar/ingestion/coingecko_fetcher.py` with `SYMBOL_TO_COINGECKO_ID` map (BTC-USD→bitcoin, ETH-USD→ethereum, SOL-USD→solana, +7 others), `/coins/{id}/ohlc` endpoint, 429-aware retry, emits `PriceBar(volume=0)` (OHLC endpoint has no volume). Gated by `FINTRACK_ENABLE_CRYPTO_JOB` (default false — yfinance still handles crypto by default). When disabled, the scheduler removes the job on next start.
- **FRED fetcher + `ingest_macro`**: `sidecar/ingestion/fred_fetcher.py` — hits `/fred/series/observations`, strips the "." missing-value sentinel, swallows per-series failures in `fetch_macro_series_many`. Job auto-skips when `FINTRACK_FRED_API_KEY` is unset; when set, runs as a daily cron (`ingest_macro_cron_hour`, default 06:00 UTC).
- **Macro data model**: `MacroIndicator` (series_id unique, name/description/units/frequency, is_active) + `MacroDataPoint` (indicator_id FK cascade, date, Numeric(20,6) value, unique `(indicator_id, date)`). Alembic `0003_create_macro.py` on top of 0002.
- **Macro seed**: `DEFAULT_MACRO_INDICATORS` = CPIAUCSL, UNRATE, FEDFUNDS, DGS10, GDP. `seed_all_defaults()` wraps assets + macro seeding; lifespan runs both on startup.
- **Macro API**: `GET /api/macro/?active_only=true` + `GET /api/macro/{series_id}/?from=&to=&limit=500`, case-insensitive symbol lookup, 404 on unknown, ascending time order. Pydantic v2 with `ConfigDict(from_attributes=True)`.
- **Verifications**: `pytest` 51/51 green, `ruff check .` clean, `mypy --strict sidecar/` clean on 24 files. Live smoke: sidecar runs through migrations 0001→0003, `/api/macro/` returns the 5 seeded indicators, `/api/macro/NOPE/` → 404

### Still deferred (out of Sprint 2 by choice)
- **`vacuum_db` weekly job** — low priority, add with Sprint 4 scheduler work

### What was completed (Sprint 3 so far)
- **3A — Plumbing (`df1c30e`)**: Tailwind v4.2.4 via `@tailwindcss/vite`; class-based dark variant via `@custom-variant`. Zustand 5 settings store (`useSettings`) with persist middleware (localStorage key `fintrack-settings`) — `theme: "system" | "light" | "dark"` + `resolveTheme()` + `applyTheme()` + `useResolvedTheme()` hook (useSyncExternalStore-based, reacts to OS prefers-color-scheme changes). Rewrote API client as `apiGet<T>` + `ApiError` with typed endpoints: `getHealth`, `listAssets`, `getPriceSeries`, `listMacroIndicators`, `getMacroSeries`. Decimal fields typed as `string` (Pydantic serialises Decimal to string); timestamps as ISO-8601 UTC.
- **3B — App shell (`5d570ac`)**: HashRouter with routes `/`, `/assets/:symbol`, `/market`, `/macro`, `/settings` — HashRouter chosen so deep-link refresh works in the Tauri webview without a SPA fallback server. `AppShell` layout = sidebar + sticky header + scrollable main. `Sidebar` with NavLink + lucide icons + active-state styling. `Header` with dynamic page title + `HealthIndicator` (moved from App.tsx, polls `/api/health/` every 2s) + `ThemeToggle` (cycles system→light→dark, reads/writes the settings store). Placeholder pages for Dashboard, Market, Macro, AssetDetail, Settings via shared `PagePlaceholder`. Retired `App.css`; fully on Tailwind. Installed `react-router-dom@7.14.2`, `lucide-react@1.8.0`.
- **3C — Dashboard (`e3fd661`)**: Parallel fan-out — `listAssets()` then `Promise.all` of `getPriceSeries(symbol, { limit: 60 })`. `AssetCard` shows symbol + name + asset-type pill, last close (tabular-nums), day change % vs previous close with colour-coded arrow, and a 60-bar inline-SVG `Sparkline`. Empty/error states rendered distinctly; Refresh button re-runs the whole pass. Card links to `/assets/:symbol` so 3D slots in without plumbing changes. Bumped tsconfig `target`/`lib` to ES2022 for `Array.prototype.at`.
- **3D — Asset detail (`1915c35`)**: `AssetDetail` fetches asset (find by symbol in `listAssets({ activeOnly: false })`) and 500 bars in parallel. Unknown-symbol, load-error, empty-bars, and loaded states all rendered. `CandleChart` wraps `lightweight-charts@5.1.0` via `createChart` + `chart.addSeries(CandlestickSeries)` + histogram for volume on an inset price scale. Theme palette switches between light/dark based on `useResolvedTheme()`. `PricePanel` shows OHLC + volume grid. News sidebar is a placeholder pointing at Sprint 4.
- **3E — Market overview (`da225d6`)**: Top 5 gainers + top 5 losers ranked by day-change %, each row linking to `/assets/:symbol`. "By asset type" breakdown counts stock/etf/crypto/commodity/index. Minimal payload — `getPriceSeries(symbol, { limit: 2 })` for each asset since we only need the last two closes. Sector heatmap deferred (Asset model carries no sector field).

### What to work on NEXT (in order)
1. [x] ~~Sprint 2 follow-ups~~ — landed as `afe3170`
2. **Sprint 3 — React UI Dashboard** (5 of 6 milestones complete)
   - [x] 3A — Plumbing: Tailwind + Zustand + typed API client (`df1c30e`)
   - [x] 3B — App shell: sidebar/header/router/theme toggle (`5d570ac`)
   - [x] 3C — Dashboard watchlist grid (`e3fd661`)
   - [x] 3D — Asset detail with Lightweight Charts (`1915c35`)
   - [x] 3E — Market overview (top movers + type counts) (`da225d6`)
   - [ ] 3F — Settings page: theme selector (client), data-source toggles + refresh intervals + DB path. Note: mutable server settings will require a new `settings` table + `/api/config/` GET/PUT endpoints or `.env` override mechanism — scope decision pending. Read-only display of current env config is the minimum viable path.
3. [ ] **Live GUI verification** — run `pnpm tauri dev` to confirm shell → sidecar round-trip still works with the new UI. Smoke test Dashboard → AssetCard click → AssetDetail candle chart, refresh, theme toggle, and nav-link active states.

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
- [x] `sidecar/db/models.py` — `Asset`, `PricePoint`, `MacroIndicator`, `MacroDataPoint` SQLAlchemy models
- [x] Alembic migration for `price_points` (0002), `macro_*` (0003)
- [x] `sidecar/ingestion/yfinance_fetcher.py` — batched `yf.download`, exponential backoff
- [x] `sidecar/ingestion/coingecko_fetcher.py` — top 10 crypto OHLC via `/coins/{id}/ohlc`
- [x] `sidecar/ingestion/fred_fetcher.py` — macro observations via FRED observations endpoint
- [x] `sidecar/scheduler/__init__.py` — APScheduler `BackgroundScheduler` + `SQLAlchemyJobStore`, misfire grace 60s
- [x] `sidecar/scheduler/jobs.py` — `ingest_prices` (5 min covering stocks/ETFs/crypto via yfinance)
- [x] `sidecar/scheduler/jobs.py` — `ingest_crypto` (15 min via CoinGecko, opt-in via `FINTRACK_ENABLE_CRYPTO_JOB`)
- [x] `sidecar/scheduler/jobs.py` — `ingest_macro` (daily 06:00 UTC via FRED, no-op without `FRED_API_KEY`)
- [x] `GET /api/assets/` — list tracked assets
- [x] `GET /api/prices/{symbol}/?from=&to=&limit=` — last N price points with date filter
- [x] `GET /api/macro/`, `GET /api/macro/{series_id}/?from=&to=&limit=` — macro indicator + series endpoints
- [x] Seed script: 10 default assets + 5 default macro indicators (CPIAUCSL, UNRATE, FEDFUNDS, DGS10, GDP)

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
- [x] Tailwind + Zustand set up in `shell/src/` (3A — `df1c30e`)
- [x] App shell: sidebar nav, header, dark mode toggle (respects OS preference) (3B — `5d570ac`)
- [x] Dashboard page: watchlist grid with sparkline + day change % (3C — `e3fd661`)
- [x] Asset detail page: full OHLCV chart (TradingView Lightweight Charts), recent news placeholder (3D — `1915c35`)
- [x] Market overview: top movers, asset-type counts (sector heatmap deferred — no sector field on Asset) (3E — `da225d6`)
- [ ] Settings page: data source toggles, refresh intervals, clear cache, DB file path (3F)

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
