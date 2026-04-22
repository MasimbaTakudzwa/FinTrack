# FinTrack — Market Intelligence Desktop App

A cross-platform desktop application for tracking market data, aggregating financial news, and (in Phase 2) running local ML forecasts. Single-user, local-only, no cloud services.

## Status

- **Phase:** 1 — Desktop scaffold & market tracking
- **Sprint:** 1 — Desktop Scaffold (Tauri shell + FastAPI sidecar + SQLite)
- **Target platforms:** macOS, Windows (Linux deferred)

## Stack

| Layer | Technology |
|-------|------------|
| Desktop shell | Tauri v2 (Rust) |
| UI | React 18 + TypeScript + Vite + Tailwind + Zustand |
| Charts | TradingView Lightweight Charts |
| Backend sidecar | FastAPI + uvicorn (Python) |
| Database | SQLite (WAL mode, in OS app-data dir) |
| Migrations | Alembic |
| Scheduler | APScheduler (in-process) |
| Data sources | yfinance, CoinGecko, FRED, Yahoo Finance RSS |

All data sources are free. Nothing is hosted. The SQLite file lives on your disk and never leaves your machine.

## Project Layout

```
FinTrack/
├── shell/              Tauri app (Rust + React)
├── sidecar/            Python FastAPI backend
├── ml/                 Phase 2 — local ML training (not yet active)
├── tests/
├── docs/
│   └── archive/        Original web-app planning docs (historical reference)
└── .claude/            Claude Code project context
```

## Development

Prerequisites:

- Python 3.11+
- Node 20+ with `pnpm`
- Rust toolchain (`rustup` — for Tauri)
- Platform-specific Tauri requirements: https://v2.tauri.app/start/prerequisites/

Full setup will be documented in `docs/development/setup.md` once Sprint 1 completes.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

FinTrack is for personal, informational use only. Market data may be delayed, cached, or inaccurate. Nothing in this application constitutes financial advice.
