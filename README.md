# FinTrack — Market Intelligence Desktop App

A cross-platform desktop application for tracking market data, aggregating financial news, and monitoring macroeconomic indicators. Single-user, local-only, no cloud services.

## What it does

- **Dashboard** — your default watchlist at a glance: last close, 24h change, 60-bar sparklines for each asset.
- **Asset detail** — full OHLCV candlestick charts (1H / 4H / 1D / 3D / 1W / All timeframes), click-to-measure tool, performance grid across multiple windows, per-asset news feed, and one-click alert creation.
- **Watchlists** — multiple named lists, drag-to-reorder, single default list pinned to the Dashboard. Add any symbol Yahoo Finance knows about.
- **News** — aggregated Yahoo Finance headlines grouped by day, filterable by symbol, linkable back to source.
- **Macro** — FRED economic indicators (CPI, unemployment, Fed funds rate, 10Y treasury, GDP) rendered as line charts with latest/previous/vs-start stats.
- **Price alerts** — set threshold alarms above or below a given price; when triggered, a native desktop notification fires and the alert lands in the in-app notification center.
- **Market overview** — top gainers and losers across your tracked assets over the last 24 hours.
- **Settings** — theme (system / light / dark), runtime scheduler intervals, FRED API key, and a read-only view of DB path + port + log level.

All data lives in a SQLite database in your OS app-data directory. Nothing leaves your machine except outbound calls to the free public data sources.

## Installation

Download the latest installer from the [releases page](https://github.com/MasimbaTakudzwa/FinTrack/releases):

| Platform | File |
|----------|------|
| macOS (Apple Silicon) | `FinTrack_<version>_aarch64.dmg` |
| Windows (x64) | `FinTrack_<version>_x64-setup.exe` or `FinTrack_<version>_x64_en-US.msi` |

### macOS

1. Open the `.dmg` and drag `FinTrack.app` to `/Applications`.
2. First launch: right-click the app → **Open** (required because current builds are unsigned; this only needs to be done once per version).
3. The app's SQLite database is stored at `~/Library/Application Support/FinTrack/fintrack.db`.

### Windows

1. Run the `-setup.exe` (NSIS) or `.msi` installer.
2. The app's SQLite database is stored at `%APPDATA%\FinTrack\fintrack.db`.

### After install

- No account, no login — the app works offline-first and fetches market data in the background.
- Optional: paste a free [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html) into **Settings → FRED API key** to enable macro indicator ingestion.

Linux is not supported in Phase 1 — it's deferred to a future release.

## Status

- **Phase 1 (desktop scaffold + market tracking):** ✅ complete. Dashboard, watchlists, news, macro, market overview, price alerts, settings — all shipping.
- **Phase 2 (local ML — sentiment analysis + price forecasting):** not started.

Signed installers, auto-updates, and Linux support are all planned for post-Phase-1 releases.

## Stack

| Layer | Technology |
|-------|------------|
| Desktop shell | Tauri v2 (Rust) |
| UI | React 19 + TypeScript + Vite + Tailwind + Zustand |
| Charts | [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) |
| Backend sidecar | FastAPI + uvicorn (Python 3.11+) |
| Database | SQLite (WAL mode, in OS app-data dir) |
| ORM + migrations | SQLAlchemy 2.x + Alembic |
| Scheduler | APScheduler (in-process, persistent jobstore) |
| Data sources | yfinance, CoinGecko, FRED, Yahoo Finance RSS |
| Packaging | PyInstaller (sidecar) + Tauri bundler (shell) |

All data sources are free. Nothing is hosted. Scheduler jobs persist across app restarts in the same SQLite file.

## Privacy

- FinTrack makes outbound network calls only to Yahoo Finance, CoinGecko, and FRED — the data sources. No telemetry, no analytics, no auth provider.
- The SQLite database file lives in your OS app-data directory. Back it up, delete it, inspect it with any SQLite client — it's yours.
- The FastAPI sidecar binds to `127.0.0.1` on a random ephemeral port picked at launch. It is never reachable off your machine.

## Development

Prerequisites:

- Python 3.11+
- Node 20+ with [`pnpm`](https://pnpm.io/)
- Rust toolchain (`rustup` — needed by Tauri)
- Platform-specific Tauri requirements: <https://v2.tauri.app/start/prerequisites/>

Clone + set up:

```bash
# Python sidecar
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# Shell
pnpm -C shell install
```

Run the full app (Tauri spawns the sidecar as a child process, picking a free localhost port at startup):

```bash
pnpm -C shell tauri dev
```

Run the sidecar standalone (useful when iterating on backend code without rebuilding the shell):

```bash
FINTRACK_PORT=8765 python -m sidecar.main
# then in the shell:
FINTRACK_EXTERNAL_SIDECAR=1 pnpm -C shell tauri dev
```

Checks:

```bash
pytest                               # sidecar tests (241 passing)
ruff check .                         # Python lint
mypy --strict sidecar/               # Python types
pnpm -C shell lint                   # TS + React lint
pnpm -C shell build                  # TypeScript + Vite build
```

Package an unsigned bundle locally:

```bash
pnpm -C shell tauri:build:unsigned
```

More on the release pipeline in [`docs/development/release_process.md`](docs/development/release_process.md).

## Project layout

```
FinTrack/
├── shell/                           Tauri app (Rust + React)
│   ├── src/                         React UI (TypeScript)
│   └── src-tauri/                   Tauri Rust config + sidecar launcher
├── sidecar/                         Python FastAPI backend
│   ├── api/                         Route modules
│   ├── db/                          SQLAlchemy engine, models, migrations
│   ├── scheduler/                   APScheduler jobs
│   ├── ingestion/                   yfinance / CoinGecko / FRED / RSS fetchers
│   └── services/                    Business logic (settings, watchlists, alerts, …)
├── ml/                              Phase 2 — local ML training (not yet active)
├── tests/                           Pytest suites
├── docs/
│   ├── development/                 Release runbook + setup notes
│   └── archive/                     Original web-app planning docs (historical)
├── sidecar.spec                     PyInstaller one-folder spec
├── requirements.txt                 Sidecar runtime deps
├── requirements-dev.txt             Test + lint deps
├── requirements-packaging.txt       PyInstaller (release machines only)
└── .claude/                         Claude Code project memory
```

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

FinTrack is for personal, informational use only. Market data may be delayed, cached, or inaccurate. Nothing in this application constitutes financial advice. You are responsible for your own trading decisions.
