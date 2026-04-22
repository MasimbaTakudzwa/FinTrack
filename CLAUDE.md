# FinTrack — Claude Code Project Memory
# =======================================
# Auto-loaded every session. Keep under 200 lines.
# Run `/project:start` at the beginning of every session.
# Run `/project:end` before closing every session.

---

## Project Identity

- **Name:** FinTrack — Market Intelligence Desktop App
- **Purpose:** Cross-platform desktop application for tracking live market data (stocks, ETFs, commodities, crypto), aggregating financial news, and (Phase 2) local ML forecasting
- **Form factor:** Native desktop app — macOS & Windows (Linux deferred)
- **Stack:** Tauri v2 + React + FastAPI (Python sidecar) + SQLite + APScheduler
- **Current phase:** Phase 1 — Desktop scaffold & market tracking
- **User model:** Single-user, local-only. No cloud services, no authentication, no external hosting

---

## Stack at a Glance

| Layer | Technology | Notes |
|-------|------------|-------|
| Desktop shell | Tauri v2 (Rust) | Lightweight, native webview, small installer |
| UI | React 18 + TypeScript + Vite + Tailwind + Zustand | Renders inside Tauri window |
| Charts | TradingView Lightweight Charts | Free, MIT, performant |
| Backend sidecar | FastAPI + uvicorn (Python) | Localhost HTTP, random ephemeral port |
| ORM | SQLAlchemy 2.x | Async where useful |
| Database | SQLite | Stored in OS app-data dir (dev: `./fintrack.db`) |
| Migrations | Alembic | Auto-run on app startup |
| Scheduler | APScheduler | Runs in-process inside sidecar |
| Data sources | yfinance, CoinGecko, FRED, Yahoo RSS | All free, no paid APIs |
| Packaging | Tauri bundler + PyInstaller | `.dmg` (Mac), `.msi` (Win) |

---

## Quick-Start Commands (local dev)

```bash
# Python sidecar (one-time)
python -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# Run sidecar standalone
python -m sidecar.main                   # FastAPI on a random localhost port

# Run full app (Tauri spawns sidecar as child)
cd shell && pnpm install && pnpm tauri dev

# Tests + lint
pytest
ruff check .
mypy sidecar/
pnpm --prefix shell lint
```

---

## Project Structure

```
FinTrack/
├── CLAUDE.md                        ← this file (auto-loaded)
├── README.md
├── LICENSE
├── .claude/
│   ├── PROGRESS.md                  ← READ THIS FIRST every session
│   ├── ARCHITECTURE.md              ← deep design (@import on demand)
│   ├── DECISIONS.md                 ← decisions log (@import on demand)
│   └── commands/                    ← session slash-commands
├── shell/                           ← Tauri app (Rust + React)
│   ├── src/                         ← React UI (TS)
│   ├── src-tauri/                   ← Tauri Rust config + sidecar launcher
│   └── package.json
├── sidecar/                         ← Python FastAPI backend
│   ├── main.py                      ← uvicorn entrypoint
│   ├── api/                         ← FastAPI route modules
│   ├── db/                          ← SQLAlchemy engine, models, migrations
│   ├── scheduler/                   ← APScheduler jobs
│   └── ingestion/                   ← yfinance / CoinGecko / FRED / RSS fetchers
├── ml/                              ← Phase 2 ONLY — local ML training
├── tests/
├── docs/
│   ├── architecture/
│   ├── development/
│   └── archive/                     ← original web-app plan docs (historical)
├── pyproject.toml
├── requirements.txt                 ← sidecar runtime deps
├── requirements-dev.txt             ← dev/test deps
└── requirements-ml.txt              ← Phase 2 ONLY — never install in Phase 1
```

---

## Active Sprint

> **Sprint 1 — Desktop Scaffold**
> Goal: Tauri window opens, FastAPI sidecar starts, SQLite initialised, health check round-trips shell → sidecar → DB end-to-end
> Tracking: @.claude/PROGRESS.md

---

## Critical Rules

1. **Read `.claude/PROGRESS.md` before writing any code.**
2. **No cloud services. No Postgres, no Redis, no Celery, no Django.** If these appear in memory or older docs, they are pre-pivot artefacts — follow the current architecture.
3. **All market data fetching lives in `sidecar/ingestion/` — never call external APIs from UI code.**
4. **SQLite path resolution:** dev → `./fintrack.db` (gitignored); prod Mac → `~/Library/Application Support/FinTrack/fintrack.db`; prod Win → `%APPDATA%\FinTrack\fintrack.db`.
5. **Sidecar binds to 127.0.0.1 only.** Never bind to 0.0.0.0. Port is chosen at runtime — never hardcode.
6. **Mark tasks [x] only after `pytest` passes AND `pnpm tauri dev` launches cleanly.**
7. **Run `/project:checkpoint` after every 2-3 completed tasks.**
8. **Run `/project:end` before ending any session.**
9. **Never commit `.env` — only `.env.example`. All secrets via environment variables.**
10. **Phase 2 ML libs (`requirements-ml.txt`) are never installed during Phase 1.**

---

## Architecture at a Glance

> Full details: @.claude/ARCHITECTURE.md

- **Single-process desktop app:** Tauri shell spawns Python sidecar as a child. Shell ↔ sidecar over localhost HTTP on a random ephemeral port
- **Data lives locally:** SQLite file in user's OS app-data directory. Full history kept — no retention policy needed
- **Scheduler in-process:** APScheduler runs inside the sidecar with a `SQLAlchemyJobStore` so jobs persist across restarts
- **Data sources (all free):** yfinance (stocks/ETFs/crypto), CoinGecko (crypto), FRED (macro), Yahoo RSS (news)
- **UI:** React 18 + TS, rendered inside Tauri webview (WKWebView on Mac, WebView2 on Win)
- **Alerts:** native desktop notifications via Tauri `notification` plugin — no email

---

## Known Gotchas

- **yfinance is unofficial** — Yahoo has broken it multiple times. Ingestion layer must be source-swappable so Alpha Vantage can be plugged in as fallback
- **CoinGecko free tier:** ~10–50 calls/min. Add exponential backoff with jitter
- **SQLite concurrency:** scheduler writes while UI reads. Always open with `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`
- **Random sidecar port:** pick a free port at Tauri startup, pass to child via `FINTRACK_PORT` env var, expose to frontend via a `get_sidecar_port()` Tauri command
- **Cross-platform paths:** `pathlib.Path` (Python) and Tauri `path` API (Rust) — never hardcode `/` or `\`
- **Laptop sleep/wake:** APScheduler `misfire_grace_time=60` — skip runs more than 60s late instead of queueing a backlog
- **Code signing:** Mac (Apple Developer, $99/yr) + Windows (EV cert or Azure Trusted Signing). Deferred to Sprint 5 — Phase 1 dev runs unsigned

---

## Context Management

- Run `/compact` when context hits ~50% (`/context` to check)
- After `/compact`, re-run `/project:start` to re-anchor
- PROGRESS.md survives compaction — always re-read from disk
