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

**Last updated:** 2026-04-22 — Session 002 (checkpoint 1 — Sprint 1 Milestones 1A–1E landed)
**Active sprint:** Sprint 1 — Desktop Scaffold
**Overall status:** 🟢 Scaffold working end-to-end; final kill-test + commit pending, then Sprint 2

### What was just completed
- **Milestone 1A — Tauri shell**: `shell/` scaffolded via `pnpm create tauri-app` (React + TS + Vite). Identifier `com.fintrack.app`, display name "FinTrack", 1280×800 window, devtools in dev only, CSP `connect-src` allows `http://127.0.0.1:* http://localhost:*`. `pnpm tauri dev` opens a blank window on Mac
- **Milestone 1B — Sidecar skeleton**: `sidecar/` with `main.py` (FastAPI + uvicorn reading `FINTRACK_PORT`), `api/health.py` returning `{"status":"ok","version":"0.1.0"}`, `config.py` (pydantic-settings `FINTRACK_` prefix), `requirements.txt` (+ `pydantic-settings`, `platformdirs`), `requirements-dev.txt`. Standalone `python -m sidecar.main` + `curl /api/health/` verified
- **Milestone 1C — DB + Alembic**: `db/engine.py` with WAL / synchronous=NORMAL / foreign_keys=ON / busy_timeout=5000 pragmas. Dev DB heuristic (repo root detection via `pyproject.toml` + `sidecar/`) falls back to platformdirs. `db/base.py` DeclarativeBase with naming convention. `db/models.py` `Asset` using `StrEnum`. Alembic wired via `db/migrations_runner.py` (programmatic `command.upgrade` with `config.attributes["db_path"]` injection). First migration `0001_create_assets` runs on sidecar startup via lifespan. Unique index `ix_assets_symbol`
- **Milestone 1D — Tauri ↔ sidecar wiring**: `src-tauri/src/lib.rs` (not `main.rs` — Tauri v2 template splits it) picks a free port via `TcpListener::bind("127.0.0.1:0")`, spawns `.venv/bin/python -m sidecar.main` as child with `FINTRACK_PORT` env, polls `/api/health/` with `ureq` (max 10s) before proceeding, exposes `get_sidecar_port` command, kills child on `RunEvent::Exit` AND on `WindowEvent::CloseRequested` → `app.exit(0)` (needed on macOS where closing the main window otherwise keeps the app alive and orphans the sidecar). `FINTRACK_EXTERNAL_SIDECAR=1` skips spawn and uses port 8765
- **Milestone 1E — Health-check UI**: `shell/src/api/client.ts` caches base URL via `invoke<number>('get_sidecar_port')`. `App.tsx` polls `getHealth()` every 2s with `AbortController` cleanup, renders green "Sidecar healthy — v0.1.0" badge or red error with retry. Manually verified on Mac: `pnpm tauri dev` → window opens → green badge appears. `./fintrack.db` created at repo root with `assets` table
- **CORS fix**: webview origin is `http://localhost:1420` in dev (Vite) and `tauri://localhost` in prod — cross-origin to the sidecar. Added `CORSMiddleware` with a fixed 4-origin allowlist (no wildcard). This resolved the initial "Sidecar unreachable" error
- Verifications: `pytest` 2/2 green, `ruff check .` clean, `mypy --strict sidecar/` clean, `cargo check` clean, `pnpm --prefix shell build` clean

### What to work on NEXT (in order)
1. [ ] **Finish 1E kill-test** — while `pnpm tauri dev` is running, close the window and confirm `pgrep -f sidecar.main` returns nothing (RunEvent::Exit → child.kill() path)
2. [ ] **Commit Sprint 1** — single atomic commit `feat(sprint-1): desktop scaffold with sidecar health check` covering shell + sidecar + tests + PROGRESS/ARCHITECTURE updates
3. [ ] **Start Sprint 2 — Market Data Pipeline**: `PricePoint` model + migration, `yfinance_fetcher` with batched `yf.download` + exponential backoff, APScheduler `BackgroundScheduler` + `SQLAlchemyJobStore` + `misfire_grace_time=60`, `ingest_prices` job (5 min), `GET /api/assets/`, `GET /api/prices/{symbol}/`, seed script for the 10 default assets

### Active blockers
- None

### Session notes
- **CORS required even on localhost**: Vite dev server origin `http://localhost:1420` ≠ sidecar origin `http://127.0.0.1:<port>` — browser treats them as cross-origin. Keep the allowlist explicit (don't use `*`) so prod `tauri://localhost` and dev `localhost:1420` are both covered
- **macOS window-close quirk**: on Mac, Tauri keeps the app alive after the last window closes (menu-bar-app behavior). Without a `WindowEvent::CloseRequested` → `app.exit(0)` handler, `RunEvent::Exit` never fires and the python child is orphaned. `pgrep -f sidecar.main` is the go-to verification
- **Dev DB path heuristic**: when CWD has `pyproject.toml` + `sidecar/`, DB is `./fintrack.db` (gitignored); otherwise `platformdirs.user_data_dir("FinTrack","FinTrack")`. Tests always pass an explicit `db_path` to `upgrade_to_head` for isolation
- **Tauri v2 template split**: entry is `src-tauri/src/lib.rs` (exports `run()`), not `main.rs` — easy gotcha when following older Tauri docs
- **StrEnum + ruff UP042**: use `from enum import StrEnum` (Python 3.11+) instead of `class X(str, Enum)`
- **Alembic `path_separator`**: newer Alembic deprecates `version_path_separator` alone — add `path_separator = os` alongside it to silence the warning
- **ESLint flat config gotcha**: `eslint-plugin-react-hooks@7` top-level `configs["recommended-latest"]` is still the legacy eslintrc shape. For flat config use `reactHooks.configs.flat["recommended-latest"]`. Also `pnpm --prefix` is an npm-only flag — pnpm uses `-C <dir>` or `--dir <dir>`
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
- [ ] Verified on Mac: killing sidecar process → badge flips to ✗ within 3s (pending — also verify `pgrep -f sidecar.main` returns nothing after window close)
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
- [ ] `sidecar/db/models.py` — `Asset`, `PricePoint` SQLAlchemy models (composite index on `asset_id, timestamp DESC`)
- [ ] Alembic migration for `price_points`
- [ ] `sidecar/ingestion/yfinance_fetcher.py` — batched `yf.download`, exponential backoff
- [ ] `sidecar/ingestion/coingecko_fetcher.py` — top 10 crypto OHLCV
- [ ] `sidecar/ingestion/fred_fetcher.py` — macro indicators (requires `FRED_API_KEY`)
- [ ] `sidecar/scheduler/__init__.py` — APScheduler `BackgroundScheduler` + `SQLAlchemyJobStore`, misfire grace 60s
- [ ] `sidecar/scheduler/jobs.py` — `ingest_prices` (5 min), `ingest_crypto` (5 min), `ingest_macro` (daily 06:00)
- [ ] `GET /api/assets/` — list tracked assets
- [ ] `GET /api/prices/{symbol}/?from=&to=&limit=` — last N price points with date filter
- [ ] Seed script: 10 default assets (AAPL, MSFT, GOOGL, NVDA, SPY, QQQ, GLD, BTC-USD, ETH-USD, SOL-USD)

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
