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

**Last updated:** 2026-04-22 — Session 003 (checkpoint 20 — macro fire-on-first-add + chunked bulk insert, triggered by user pasting their FRED key)
**Active sprint:** Sprint 5 — Packaging & Distribution (freeze ✅, bundle ✅, CI workflow ✅, Release workflow ✅, updater plugin ✅, release docs ✅, PR #1 merged ✅, PR #2 merged ✅, v0.1.0 tagged ✅, draft release cut ✅, CI-built `.dmg` smoke-tested ✅, README refreshed for Phase 1 completion ✅ (PR #4), Macro page built out ✅ (PR #3), macro-fire-on-first-add fix merged ✅ (PR #6); publish button + optional GUI smoke pass pending user decision) — plus a belated Sprint 3 follow-up: Macro page built out (was a placeholder).
**Overall status:** 🟢 Sprints 1–4 complete. Sprint 5 effectively done — v0.1.0 draft release sitting on GitHub with three installers attached: `FinTrack_0.1.0_aarch64.dmg` (48 MB), `FinTrack_0.1.0_x64_en-US.msi` (50 MB), `FinTrack_0.1.0_x64-setup.exe` (39 MB). No updater bundles (expected — `TAURI_SIGNING_PRIVATE_KEY` not yet set). CI `.dmg` verified: mounts clean, `FinTrack.app` carries the right identifier `com.fintrack.app` + version 0.1.0, adhoc-signed (unsigned build, as expected), frozen sidecar inside bundles correctly and boots to `/api/health/` in ~3 s. The last click is publishing the draft → live.

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
- **3F — Settings (full path)**: Full mutable runtime-settings stack.
  - **DB layer**: new `settings(key PK, value TEXT, updated_at)` table via Alembic `0004_create_settings.py`; `Setting` SQLAlchemy model.
  - **Service layer** (`sidecar/services/settings.py`): `SETTINGS_SPECS` declares 5 mutable keys — `ingest_prices.interval_minutes` (int, 1–1440), `ingest_crypto.enabled` (bool), `ingest_crypto.interval_minutes` (int, 1–1440), `ingest_macro.cron_hour_utc` (int, 0–23), `fred_api_key` (secret). Each spec carries type, env_attr, default, min/max, label, description. `load_effective_config()` merges **DB > env > default**. `validate_and_serialize()` type-checks + bounds-checks; `apply_updates()` is atomic (validates all before any write); empty-string for SECRET type deletes the DB row (reverts to env/default).
  - **API**: `GET /api/config/` returns `{settings: [...with source/env_name/min/max/has_value/masked-secret...], readonly: {db_path, port, log_level, enable_scheduler, enable_seed}}`. `PUT /api/config/` takes `{updates: {key: value}}`, 422s on validation failure (atomic — no partial writes), then calls `scheduler.reconfigure()` best-effort.
  - **Scheduler refactor**: `_register_jobs(scheduler, config)` takes an effective-config dict instead of reading `sidecar.config.settings` directly. New `reconfigure()` uses the module-level scheduler lock and re-runs `_register_jobs` with fresh effective config — APScheduler `add_job(replace_existing=True)` updates intervals in place, `remove_job` (wrapped in `suppress(JobLookupError)`) drops disabled jobs. `ingest_macro` job reads `fred_api_key` from effective config on each invocation so runtime key changes take effect without restart.
  - **Shell/UI**: `apiPut<T,B>` helper with JSON-body error-detail extraction. `getConfig()`, `putConfig()` in `shell/src/api/client.ts`. `Settings.tsx` rewrite: theme radio (system/light/dark, client-only, bound to `useSettings`), backend settings list (bool → toggle, int → number input w/ min/max, secret → password field + conditional Clear button, colour-coded source badge — zinc default / indigo env / emerald db), read-only runtime info panel, sticky dirty-state save bar with revert. Dirty detection via `collectDirty()` — `null` = untouched, `""` on a secret with stored value = intent to clear.
  - **Tests**: `test_migrations.py` adds settings-table check; `test_settings_service.py` covers precedence (default/env/db), int bounds, bool string parsing, atomic failure, secret clear, `reset_to_default`; `test_api_config.py` covers GET shape, masked secret, PUT int/bool/secret, 422 validation, empty-secret clearing, atomic failure; `test_scheduler_reconfigure.py` spins up a real `BackgroundScheduler` in `paused` mode (jobs actually persist to jobstore) — covers add, enable-toggle add/remove, in-place interval update, cron-hour change, reconfigure on non-running scheduler returns False, reconfigure after service `apply_updates` picks up new values.
  - **Verifications**: `pytest` 80/80 green, `ruff check .` clean, `mypy --strict sidecar/` clean on 28 files, `pnpm lint` clean, `pnpm build` clean (437 kB JS / 138 kB gzipped).

### Post-3F bug fix — cold-start empty dashboard
- **Symptom**: on `pnpm tauri dev`, Dashboard sparklines showed 0 bars and AssetDetail showed "no data" even after clicking Refresh — the backend genuinely had 0 `price_points` rows (verified via `sqlite3 fintrack.db "SELECT COUNT(*) FROM price_points"`). The DB path wasn't the issue: Tauri's `spawn_sidecar` already calls `.current_dir(&root)` so the repo-root `./fintrack.db` is used correctly.
- **Root cause**: APScheduler's `IntervalTrigger(minutes=5)` schedules the FIRST fire at `now + 5 min`, not immediately on scheduler start. So for the first 5 minutes after a cold launch, no bars exist — the same would happen on every fresh machine.
- **Fix (`sidecar/scheduler/__init__.py`)**: pass `next_run_time=datetime.now(UTC)` to the two interval jobs (`ingest_prices`, `ingest_crypto`) inside `_register_jobs`. Fires once immediately when the scheduler starts, then settles into normal cadence. Cron-triggered `ingest_macro` is intentionally unchanged — it should honour its scheduled hour, not fire on every start. Side-benefit: when the user saves new interval values from Settings, `reconfigure()` → `_register_jobs()` now also triggers an immediate refresh, which matches user intent ("I changed the interval, show me new data").
- **New test** (`test_register_jobs_fires_interval_jobs_immediately_on_first_register`) asserts `next_run_time` for both interval jobs lands inside the `_register_jobs()` call window (±1 s).
- **Verifications**: `pytest` 81/81 green, `ruff check .` clean, `mypy --strict sidecar/` clean on 28 files.

### What was completed (Sprint 4A — news ingestion pipeline, backend)
- **Article + ArticleAsset models** (`sidecar/db/models.py`): `Article` keyed on `url` (unique, 1024 chars, indexed) — stores `headline` (512 chars), `source` (128 chars), `published_at` (tz-aware, indexed), nullable `summary` (Text), `created_at`. `ArticleAsset` is the M2M link table with composite PK `(article_id, asset_id)`, both FKs `ON DELETE CASCADE`, asset_id indexed for filter-by-symbol queries. `Article.assets` relationship via `secondary="article_assets"`, `backref="articles"`.
- **Alembic migration 0005** (`sidecar/db/migrations/versions/0005_create_articles.py`): creates `articles` + `article_assets` with indexes `ix_articles_url`, `ix_articles_published_at`, `ix_article_assets_asset_id`. Clean up on downgrade.
- **RSS fetcher** (`sidecar/ingestion/rss_fetcher.py`): `fetch_news_for_symbol(symbol)` hits `https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}&region=US&lang=en-US` via `requests.get` (configurable timeout) then `feedparser.parse(bytes)`. `NewsItem` dataclass holds `(url, headline, source, published_at, summary, symbol)`. `_parse_published` converts `published_parsed`/`updated_parsed` struct_time → UTC datetime, tolerates missing fields. Headlines truncated at 512 chars; source at 128. Entries missing URL, headline, or pubDate are dropped. `source` comes from per-entry `entry.source["title"]` when set; otherwise falls back to the literal `"Yahoo Finance"`. Exponential backoff with jitter (base 1s, cap 15s, `MAX_ATTEMPTS=3`) via `_backoff_sleep`. `fetch_news_for_many(symbols)` iterates and swallows per-symbol `RSSFetcherError` so one bad symbol doesn't kill the batch. `feedparser` 6.0.12 ships typed stubs so no `# type: ignore` needed.
- **ingest_news job** (`sidecar/scheduler/jobs.py`): `_upsert_articles()` uses SQLite `INSERT ... ON CONFLICT(url) DO NOTHING` to dedup by URL, then `SELECT url, id` for the batch to hydrate IDs for both new + existing. `_upsert_article_assets()` inserts composite-PK pairs with `ON CONFLICT(article_id, asset_id) DO NOTHING`. Full job flow: load active symbols → `fetch_news_for_many` → map back to asset IDs → upsert articles → upsert associations. Returns `(articles_inserted, links_inserted)`. Short-circuits when no active assets.
- **Config + settings**: new `enable_news_job: bool = True` and `ingest_news_interval_minutes: int = 15` in `sidecar/config.py`. Two new entries in `SETTINGS_SPECS` (`sidecar/services/settings.py`) — `ingest_news.enabled` (BOOL, env_attr `enable_news_job`) and `ingest_news.interval_minutes` (INT, 1–1440). Brings total mutable settings to 7.
- **Scheduler registration** (`sidecar/scheduler/__init__.py`): `_register_jobs` gates `ingest_news` on the effective-config flag (adds or removes accordingly), uses `IntervalTrigger(minutes=config["ingest_news.interval_minutes"])` with `next_run_time=now` so the first run fires immediately on cold start / reconfigure — same pattern as prices/crypto.
- **API** (`sidecar/api/news.py`): `GET /api/news/?symbol=&from=&to=&limit=` returns `{count, articles: [{id, url, headline, source, published_at, summary, symbols[]}]}`. Newest-first, `limit` bounded 1–500 (default 50). Symbol filter is case-insensitive, 404 on unknown symbol. Two-query hydration: load articles for the filter, then `SELECT article_id, assets.symbol FROM article_assets JOIN assets` scoped to those article_ids, group into lists. Wired into `sidecar/main.py` via `app.include_router(news_router)`.
- **Tests added** (21 new, total 102):
  - `tests/test_migrations.py`: asserts 0005 creates both tables with correct columns, composite PK, FKs, and both indexes.
  - `tests/test_rss_fetcher.py` (5): parse real-shaped RSS XML, skip entries missing critical fields, truncate 512-char headline, retry-then-raise after `MAX_ATTEMPTS`, `fetch_news_for_many` swallows per-symbol errors.
  - `tests/test_ingest_news.py` (4): insert+link happy path, dedup on same URL, multi-asset linking (one article → two ArticleAsset rows), no-active-assets short-circuit.
  - `tests/test_api_news.py` (9): all-newest-first, symbols hydration, filter by symbol, case-insensitive, 404 unknown, date range, limit clamp, empty DB, limit validation 422.
  - `tests/test_scheduler_reconfigure.py`: DEFAULT_CONFIG expanded with news keys; added tests for news default-on, news enable-toggle removal; immediate-fire test now iterates prices/crypto/news.
  - `tests/test_api_config.py` / `tests/test_settings_service.py`: expected-key counts bumped to 7.
- **Live smoke test**: ran `upgrade_to_head()` then `ingest_news()` against the worktree DB — **162 unique articles ingested, 200 (article, asset) links** (20 per symbol across all 10 seeded assets). Re-run produced 0 new articles, 0 new links — idempotent.
- **Verifications**: `pytest` 102/102 green, `ruff check .` clean, `mypy --strict sidecar/` clean on 31 files.

### What was completed (Sprint 4B — News UI)
- **API client** (`shell/src/api/client.ts`): new `Article` + `ArticleList` TS interfaces mirroring the backend shape; `listNews({ symbol?, from?, to?, limit?, signal? })` helper that GETs `/api/news/` and returns `ArticleList`.
- **Reusable `NewsList` component** (`shell/src/components/NewsList.tsx`): pure render of `Article[]` with loading / error / empty states. Each row shows headline (links to `article.url` in external browser via `target="_blank" rel="noopener noreferrer"` + `ExternalLink` icon), source, relative-time-ago (`just now` / `Nm ago` / `Nh ago` / `Nd ago` / `YYYY-MM-DD` for > 7d), and symbol chips that link back to `/assets/:symbol`. Supports two densities (`compact` for sidebar, `comfortable` for page) and `hideSymbol` to drop redundant chips on AssetDetail. Relative-time parser accepts both ISO-with-offset and naive-UTC strings (backend's SQLite DateTime returns naive — we coerce with trailing `Z`).
- **AssetDetail sidebar** (`shell/src/pages/AssetDetail.tsx`): replaced `NewsPanelPlaceholder` with a real `NewsPanel` that fetches `listNews({ symbol, limit: 10 })`. Panel is keyed on `asset.symbol` so navigating between asset pages remounts it with fresh `{loading: true, articles: []}` state (cleaner than synchronously resetting state in a `useEffect`, which the new `react-hooks/set-state-in-effect` rule forbids). Header shows `Recent news` + a `See all →` link to `/news?symbol={SYMBOL}`.
- **`/news` standalone page** (`shell/src/pages/News.tsx`): full-page news view with a symbol-filter `<select>` (All / every known asset, sorted alphabetically by symbol), a Refresh button, and a grouped list (Today / Yesterday / ISO-date sections). Filter state lives in the URL as `?symbol=AAPL` via `useSearchParams`, so the `See all →` deep-link from AssetDetail works and refresh preserves context. Article count in subheader shows the scope. Empty / error / loading handled by delegating to `NewsList`.
- **Routing + nav**: new `/news` route in `shell/src/App.tsx`; `Newspaper` NavLink added to `Sidebar.tsx` between Market and Macro; `Header.tsx` `titleForPath` handles `/news` → `"News"`.
- **React 19 hook rule**: `react-hooks/set-state-in-effect` (new in this ESLint config) forbids synchronous `setState` inside a `useEffect` body. Fix pattern: only call `setState` from `.then`/`.catch` handlers (or post-`await` in an async IIFE); use `key={…}` resets to force a fresh `loading: true` initial state; handle loading-on-refresh / loading-on-filter-change inside the event handlers (which are not governed by the rule).
- **Verifications**: `pnpm -C shell lint` clean, `pnpm -C shell build` clean (444 kB JS / 140 kB gzipped — up from 437/138 with 4B additions). Backend unchanged — `pytest` still 102/102.

### What was completed (Sprint 4C — Watchlists, commit `0bfce3d`)
- **Models** (`sidecar/db/models.py`): `Watchlist` (id, unique `name`, `is_default` boolean default False, `created_at`, relationship `items` → `WatchlistItem` w/ `cascade="all, delete-orphan"`, ordered by `position`). `WatchlistItem` (id, `watchlist_id` + `asset_id` FKs both `ON DELETE CASCADE`, `position` int, composite unique `uq_watchlist_items_list_asset` on `(watchlist_id, asset_id)`).
- **Alembic 0006** (`sidecar/db/migrations/versions/0006_create_watchlists.py`): creates both tables and a **partial unique index** `ux_watchlists_default_one` on `watchlists (is_default)` with `sqlite_where=sa.text("is_default = 1")` — DB-level guarantee that at most one watchlist is `is_default=True`.
- **Service layer** (`sidecar/services/watchlists.py`, new): full CRUD + seed logic. Exception hierarchy (all `*Error`-suffixed per ruff N818): `WatchlistError` (validation base), `WatchlistNotFoundError`, `WatchlistNameConflictError`, `CannotDeleteDefaultError`, `AssetNotFoundError`, `ItemAlreadyExistsError`, `ItemNotFoundError`. Dataclasses for API-facing shapes (`WatchlistSummary`, `WatchlistItemDetail`, `WatchlistDetail`). Atomic `set_default(id)`: demotes all existing defaults first in the same transaction **before** promoting the target, satisfying the partial unique index. `create_watchlist(name, is_default=False)` normalizes whitespace, enforces 1–128 chars, demotes previous default if promoting. `delete_watchlist(id)` blocks default deletion (`CannotDeleteDefaultError`). `add_item(list_id, asset_id)` appends at `_next_position`, rejects duplicates and unknown asset/list. `remove_item` re-densifies positions (0,1,3,5 → 0,1,2,3). `reorder_items(list_id, asset_ids)` requires an exact permutation — missing, extra, or duplicate IDs all raise `WatchlistError`. `seed_default_watchlist()` is idempotent: creates the "Default" watchlist on first run with all active assets alphabetically sorted, and on subsequent runs **backfills** newly-added assets at the next position (existing items keep their positions).
- **API** (`sidecar/api/watchlists.py`, new): `GET /api/watchlists/` (list, default first then alpha), `POST /api/watchlists/` (create, optional `is_default`), `GET /api/watchlists/default/` (404 if no default), `GET /api/watchlists/{id}/`, `PUT /api/watchlists/{id}/` (rename and/or set default), `DELETE /api/watchlists/{id}/`, `POST /api/watchlists/{id}/items/` (add by `asset_id`), `DELETE /api/watchlists/{id}/items/{asset_id}/`, `PUT /api/watchlists/{id}/items/reorder` (takes `{asset_ids: number[]}`). Exception → HTTP mapping: `*NotFoundError` → 404, `*ConflictError` / `CannotDeleteDefaultError` / `ItemAlreadyExistsError` → 409, `WatchlistError` → 400. Wired into `sidecar/main.py`; lifespan now calls `seed_default_watchlist()` after `seed_all_defaults()`.
- **Shell / UI**:
  - `shell/src/api/client.ts`: added `apiPost`, `apiDelete`, shared `apiJson` helper, `_detail` error-body extractor. New TS types `WatchlistSummary`, `WatchlistItem`, `WatchlistDetail`, `WatchlistList` and helpers `listWatchlists`, `getDefaultWatchlist`, `getWatchlist`, `createWatchlist`, `updateWatchlist`, `deleteWatchlist`, `addWatchlistItem`, `removeWatchlistItem`, `reorderWatchlistItems`.
  - `shell/src/pages/Watchlists.tsx` (new): two-column layout. Left sidebar lists watchlists (default pinned, star icon); hover actions for promote-to-default, rename (Pencil), delete (Trash2 — hidden on default). Inline "New watchlist" form with Enter/Escape. Right panel shows the selected watchlist's items with: an add-asset `<select>` that filters out already-added assets, a drag-reorder list powered by `@dnd-kit/core` + `@dnd-kit/sortable` (`DndContext` + `SortableContext` + `verticalListSortingStrategy`, `SortableItemRow` with `useSortable({ id: asset_id })` and a `GripVertical` drag handle). Reorder is **optimistic** — UI commits the new order immediately, and reverts + surfaces an error message if `reorderWatchlistItems()` fails.
  - `shell/src/pages/Dashboard.tsx`: pivoted to read from the default watchlist. `loadAll()` calls `getDefaultWatchlist(signal)` first; on `ApiError` 404 it falls back to all active assets (first-run guard before seed completes). Asset order comes from `detail.items[].asset_id` mapped through a `Map(allAssets)` lookup. Header shows the watchlist name, subtitle shows item count + list context, a "Manage" link routes to `/watchlists`. Empty-state CTA distinguishes "no default exists" (tells user to run seed) from "default exists but empty" (shows "Add assets to {name}" CTA linking to `/watchlists`).
  - Route + nav wiring: new `/watchlists` route in `shell/src/App.tsx`; `Star` NavLink added to `Sidebar.tsx` between Dashboard and Market; `Header.tsx` `titleForPath` handles `/watchlists` → `"Watchlists"`.
  - New deps: `@dnd-kit/core@^6.3.1`, `@dnd-kit/sortable@^10.0.0`, `@dnd-kit/utilities@^3.2.2`.
- **Tests** (51 new, total **153**):
  - `tests/test_migrations.py`: `test_upgrade_to_head_creates_watchlist_tables` asserts both tables, columns, composite unique constraint, `ux_watchlists_default_one` partial index, CASCADE FKs.
  - `tests/test_watchlists_service.py` (28): seed idempotency + backfill (stable positions for pre-existing items), CRUD, rename + strip/validate, set_default promote/demote invariants, create-as-default demotes previous, delete-default forbidden, cascade delete of items, add-item positions + duplicates + unknown-asset/list, remove-item re-densification, reorder happy path + rejects missing/extra/duplicate IDs, list ordering (default first then alpha), `get_default_watchlist() is None` when no default, **DB-level partial-unique-index enforcement** by smashing two `is_default=True` rows in directly.
  - `tests/test_api_watchlists.py` (22): all HTTP routes + 404/409/400/422 error mappings.
- **Verifications**: `pytest` 153/153 green, `ruff check .` clean, `mypy --strict sidecar/` clean on 34 files, `pnpm -C shell lint` clean, `pnpm -C shell build` clean (504 kB JS / 159 kB gzipped — up from 444/140 with Watchlists page + @dnd-kit).

### What was completed (Sprint 4D — Price alerts + desktop notifications, commit `1e89596`)
- **Models** (`sidecar/db/models.py`): `AlertDirection` StrEnum (ABOVE/BELOW) + `PriceAlert` model — `asset_id` FK with `ON DELETE CASCADE`, `threshold` `Numeric(18,6)`, SQLEnum with `values_callable=lambda e: [m.value for m in e]` (so SQLite stores `"above"`/`"below"`, not `"ABOVE"`), `is_active` default True, nullable `triggered_at`/`notified_at` (both tz-aware DateTime), `note` String(256), `created_at`.
- **Alembic 0007** (`sidecar/db/migrations/versions/0007_create_price_alerts.py`): creates `price_alerts` + three indexes tuned for the scheduler's scan paths — `ix_price_alerts_asset_id` (FK lookups), `ix_price_alerts_active_pending` on `(is_active, triggered_at)` for the check-alerts scan, `ix_price_alerts_notify_pending` on `(triggered_at, notified_at)` for the pending-notifications poll.
- **Service layer** (`sidecar/services/alerts.py`, new): exception hierarchy — `AlertError(ValueError)` base, `AlertNotFoundError`, `AssetNotFoundError`. `AlertOut` dataclass hydrated with `symbol`, `asset_name`, `last_price`, `last_price_at` (latest `PricePoint.close` via a correlated subquery per asset). Public functions: `list_alerts(asset_id, active_only)`, `get_alert`, `list_pending_notifications` (triggered_at NOT NULL AND notified_at IS NULL), `create_alert`, `update_alert`, `delete_alert`, `mark_notified` (idempotent — refuses to stamp if not triggered; second call is a no-op), `check_alerts()` (scheduler entry: scans active+untriggered rows, stamps `triggered_at` when the latest close crosses the threshold inclusively). `update_alert()` uses a `update_note: bool = False` flag pair so the API layer can distinguish "note omitted" (PATCH leaves it alone) from "note=null" (explicit clear) — mypy-friendly alternative to a sentinel.
- **Scheduler job** (`sidecar/scheduler/jobs.py` + `sidecar/scheduler/__init__.py`): new `check_price_alerts` wrapper around `services.alerts.check_alerts` (logs+swallows exceptions). Registered with `IntervalTrigger(minutes=check_alerts.interval_minutes)` + `next_run_time=now` for immediate first fire. Gated by new `check_alerts.enabled` setting (default on) with add-or-remove-job pattern via `contextlib.suppress(JobLookupError)`.
- **Config + settings**: two new entries in `SETTINGS_SPECS` — `check_alerts.enabled` (BOOL, env_attr `enable_alerts_job`, default True) and `check_alerts.interval_minutes` (INT, 1–60, default 1). Total mutable settings now **9**. `sidecar/config.py` gets matching `enable_alerts_job: bool = True` and `check_alerts_interval_minutes: int = 1`.
- **API** (`sidecar/api/alerts.py`, new): Pydantic v2 schemas — `AlertOutModel`, `AlertListOut`, `CreateAlertIn`, `UpdateAlertIn` (with `model_config = {"extra": "forbid"}` → PUT with unknown fields returns 422). Routes: `GET /api/alerts/?asset_id=&active_only=`, `GET /api/alerts/pending-notifications/`, `POST /api/alerts/` (201), `GET /api/alerts/{id}/`, `PUT /api/alerts/{id}/`, `DELETE /api/alerts/{id}/` (204), `POST /api/alerts/{id}/mark-notified/`. Uses `"note" in body.model_fields_set` to pass `update_note=True` only when the client sent the key. Service exceptions → HTTP: `*NotFoundError` → 404, `AlertError` (including non-positive threshold, bad direction, not-triggered mark) → 400 (422 for Pydantic validation errors). Wired into `sidecar/main.py`.
- **Shell side**:
  - `shell/src/api/client.ts`: new types `AlertDirection`, `PriceAlert`, `AlertList`, `CreateAlertBody`, `UpdateAlertBody` and helpers `listAlerts`, `listPendingAlertNotifications`, `getAlert`, `createAlert`, `updateAlert`, `deleteAlert`, `markAlertNotified`.
  - **Tauri notification plugin**: added `tauri-plugin-notification = "2"` to `Cargo.toml`, registered in `lib.rs` via `.plugin(tauri_plugin_notification::init())`, added `"notification:default"` permission to `capabilities/default.json`, installed `@tauri-apps/plugin-notification@2.3.3` in the shell package.
  - **`useAlertNotifier` hook** (`shell/src/hooks/useAlertNotifier.ts`): mounted once by `AppShell`. Polls `/api/alerts/pending-notifications/` every **30 s** (first tick at ~1.5 s after mount so launches get alerts fast), fires `sendNotification({title, body})` via the plugin (title = `"{symbol} ↑/↓ {threshold}"`, body = `"{name} rose above/dropped below {threshold} at {price} — {note}"`), then POSTs `markAlertNotified(id)`. Requests permission once on first tick; if denied, continues polling but silently skips `sendNotification` (still marks as notified so the queue drains). Resilient: any thrown error (network blip, plugin unavailable, sidecar restart) is logged and the next tick retries. Crash between fire and mark → replays on next poll, at worst a duplicate ping, never a lost alert.
  - **`AlertCreateModal`** (`shell/src/components/AlertCreateModal.tsx`): modal launched from AssetDetail's new "Create alert" button. Direction toggle (above ↑ / below ↓), numeric threshold input prefilled with the last close, optional 256-char note, Escape-to-close, click-backdrop-to-close, error surface inline, `onCreated` callback shows a success banner on the page linking to `/alerts`.
  - **`/alerts` page** (`shell/src/pages/Alerts.tsx`): filter tabs (all / active / triggered) with counts, refresh button, table with asset, direction+threshold, last price, status chip (Armed / Triggered / Paused), triggered-at timestamp (localised), note, actions: Pause/Resume (toggles `is_active`), Reset (clears both timestamps — visible only when triggered), Delete (with confirm). Triggered rows tinted amber. Optimistic local updates after each PUT — UI updates immediately from the returned `PriceAlert`, no full refetch.
  - Route + nav wiring: new `/alerts` route in `App.tsx`; `Bell` NavLink added to `Sidebar.tsx` between Macro and Settings; `Header.tsx` `titleForPath` handles `/alerts`.
- **Tests** (**215 total**, +62 over 4C): `test_alerts_service.py` (~32) — create happy + decimal/float coercion + non-positive rejection + bad direction + unknown asset + note 256-char limit + strip-blank-to-null; list newest-first + filter by asset + active_only + hydrate `last_price` (null when no bars); get 404; update all fields individually + reset clears both timestamps + note flag semantics (omitted vs. null vs. value) + validation; delete + 404 + asset-FK CASCADE; `check_alerts()` — above fires, above-under no-fire, below fires, below-over no-fire, **equal-threshold fires inclusively**, skips inactive, skips no-price-data, uses the most-recent bar when several exist, multi-asset independence; pending-notifications handshake — filter by triggered/not-notified, mark_notified requires triggered (raises `AlertError`) + idempotent + 404. `test_api_alerts.py` (~20) — all HTTP routes + status codes, `last_price` hydration, filter query params, 404/422/400 error mapping, extra=forbid on PUT, full pending-notifications end-to-end (trigger → poll → mark → gone). `test_migrations.py` — asserts `price_alerts` columns, all three indexes, CASCADE FK to assets. `test_scheduler_reconfigure.py` — DEFAULT_CONFIG extended with the two new keys; added `test_register_jobs_adds_check_alerts_by_default` + `test_register_jobs_removes_disabled_check_alerts`; immediate-fire test iterates prices/crypto/news/**check_price_alerts**. `test_settings_service.py` + `test_api_config.py` — expected-key counts bumped from 7 → 9.
- **Verifications**: `pytest` 215/215 green, `ruff check .` clean, `mypy --strict sidecar/` clean on 37 files, `pnpm -C shell lint` clean, `pnpm -C shell build` clean (**521 kB JS / 163 kB gzipped** — up from 504/159 with Alerts page + modal + notification plugin bindings). `cargo check` on `shell/src-tauri` compiles clean with the new plugin.

### What was completed (post-Sprint-4 polish — 8-item user-reported list)
User raised 8 items after confirming Sprint 4 worked live. Worked through all of them as a single session:

- **Bug — watchlist delete button (and alert delete)**: root cause was `window.confirm()` being silently suppressed in the Tauri webview (WKWebView on macOS). Replaced with an in-app `ConfirmDialog` component (modal with Cancel/Delete + Escape-to-close + click-backdrop-to-close). Also promoted delete from hover-only to always-visible on both pages so users can reach it without guessing. Same fix shape on `/watchlists` and `/alerts`.
- **Bug — empty top losers on Market overview**: `getPriceSeries(symbol, { limit: 2 })` was pulling the two most-recent 5-min bars, which are frequently *identical* (after-hours, low-volume windows, or the minute the scheduler ran) so every asset's day-change % was 0 or positive and no losers ever surfaced. Pivoted to "earliest bar from the last ~24h (or the oldest available when sparser)": `getPriceSeries(symbol, { limit: 300 })` then client-side pick of the anchor close for the change window. Losers now populate even on low-volume sessions.
- **Feature — arbitrary-asset lookup + add (biggest piece)**:
  - **Backend** (`sidecar/services/assets.py`, new): `resolve_symbol(symbol)` hits `yf.Ticker.fast_info` first (fast, returns quote_type/currency/exchange/last_price) and falls back to `.info` for display name. `_QUOTE_TYPE_MAP` converts yfinance `quoteType` strings (`EQUITY`/`ETF`/`CRYPTOCURRENCY`/`INDEX`/`FUTURE`/`COMMODITY`/`MUTUALFUND`/`CURRENCY`) to the DB's `AssetType` enum. When metadata is empty (new/obscure listings), attempts a 5-day download via the existing `fetch_prices` to confirm liveness. `add_asset()` persists + kicks off a one-shot `ingest_prices_for_symbols([symbol])` *outside* the session scope (so the transaction commits before ingest starts). Exceptions `AssetServiceError` / `SymbolNotFoundError` / `AssetAlreadyExistsError` map cleanly to HTTP 400/404/409.
  - **API** (`sidecar/api/assets.py`): `POST /api/assets/lookup/` (preview without persisting — returns resolved name/type/exchange/currency) + `POST /api/assets/` (persist + optional `add_to_default_watchlist: boolean`). Pydantic schemas validate symbol is 1–32 chars. Watchlist link failure is non-fatal (logged) so a flaky watchlist doesn't break asset creation. Response returns the hydrated `asset` + `bars_ingested` count + `added_to_watchlist` boolean.
  - **UI** (`shell/src/components/AddAssetModal.tsx`, new): two-step flow. Type a symbol → Enter / Lookup button → preview card with resolved name + type pill + exchange + currency. Preview is invalidated when typed symbol diverges from resolved. Add button → POST + toast-style success banner on the calling page. Handles 404 ("Symbol X not found on Yahoo Finance") and 409 ("X is already tracked") with friendly messages.
  - **Entry points**: "Add asset" button in Dashboard header (indigo primary). "Track new…" button on `/watchlists` (white secondary, added next to existing watchlist-add dropdown). When adding from a non-default watchlist, the new asset is *also* auto-added to that list after creation.
  - Tests: 26 new across `test_assets_service.py` (fast_info/info interplay, fallback download rescue, case-insensitive dedup, ingest failure is non-fatal, quote_type mapping) and `test_api_assets.py` (lookup preview shape, create + auto-link-to-default, 404/409/422 mappings, no-default-watchlist is non-fatal).
- **AssetDetail full rebuild** (three features in one coherent redesign) — `shell/src/pages/AssetDetail.tsx` rewritten from ~150 LOC to ~940 LOC:
  - **Timeframe toggle** (1H / 4H / 1D / 3D / 1W / All): client-side slicing via `sliceToTimeframe(points, tf)` — cutoff based on last bar's timestamp. `MAX_BARS = 3000` pulled upfront; each timeframe is a windowed slice. yfinance's 5-min history caps ~60 days so 3000 bars is also a soft ceiling; when the daily-bar pipeline lands we can extend to longer windows. Timeframe change clears any active measurement to avoid stale markers.
  - **Measurement tool**: `CandleChart` gained a new `measure` prop with `first`, `second`, `onClick` (parent owns state). Chart `subscribeClick` + `candleSeries.coordinateToPrice(y)` converts screen clicks to (time, price). `createSeriesMarkers(candle, [])` draws A/B markers. Parent renders `MeasureReadout` — emerald when Δ > 0, rose when Δ < 0, indigo while waiting for B, with formatted Δ$ + Δ% + duration (s/m/h/d). Third click starts a new pair. Subscribe/unsubscribe don't resubscribe on every render — `onClickRef` pattern keeps the callback fresh.
  - **Below-chart fill**: formerly a blank sidebar + single Latest-bar panel. Now a coherent stack:
    1. **`PerformancePanel`** (full-width): 7-cell grid of % changes across 15m / 1h / 4h / 1d / 3d / 1w / All. Each cell computed against the anchor bar nearest the cutoff (uses all bars, not the timeframe slice).
    2. **3-column grid**: `StatsPanel` (High/Low/Range%/Window Δ%/Avg vol-per-bar for the *selected* timeframe, so switching timeframes re-renders the stats), `AlertsForAssetPanel` (armed/triggered counts + top 5 alerts for this asset + "+ New" shortcut to the create modal + "See all N →" link when overflow), `LatestBarPanel` (OHLCV — preserved from original).
  - Keying: `AlertsForAssetPanel` re-fetches on `lastCreatedAlert?.id` change, so creating an alert via the modal updates the panel without a page reload.
- **In-app NotificationCenter** (addresses "alerts show up as Terminal" + gives persistent history):
  - **`shell/src/stores/useNotifications.ts`** (new): Zustand store with `lastSeenAt: number` (persisted to localStorage as `fintrack-notifications`) and `markAllSeen()`. Any alert with `triggered_at > lastSeenAt` counts as unread; opening the dropdown bumps `lastSeenAt` to `Date.now()`.
  - **`shell/src/components/NotificationCenter.tsx`** (new): Bell-with-badge button in the Header that opens a dropdown panel. Polls `listAlerts()` every 60s + initial 1.5s delay (lets sidecar come up on cold Tauri launch), filters client-side to `triggered_at !== null`, sorts by `triggered_at` desc. Unread rows get an amber left border + background tint. Click-outside and Escape both close. Shows up to 12 rows, then "See all N →" link to `/alerts`. Empty state links to `/alerts` to create one. Row click deep-links to `/assets/{symbol}`.
  - **Why it exists**: the "Terminal" attribution is a dev-mode quirk — `target/debug/shell` has no bundle identifier set, so macOS's `NSUserNotificationCenter` attributes the notification to the spawning parent. In a bundled `.app` (`identifier: "com.fintrack.app"` is already correct in `tauri.conf.json`) the OS notification will show "FinTrack" — this resolves automatically in Sprint 5. But even after that, the in-app bell is more useful: persistent history, works when OS perms are denied, no attribution dependency.
- **Verifications** (post-session sweep): `pytest` **241/241** green (+26 over Sprint 4D's 215), `ruff check .` clean, `mypy --strict sidecar/` clean on 38 files, `pnpm -C shell lint` clean, `pnpm -C shell build` clean (**557 kB JS / 172 kB gzipped** — up from 521/163 with AddAssetModal + AssetDetail rebuild + NotificationCenter).

### What was completed (Sprint 5 start — PyInstaller freeze + Tauri bundle wiring)
- **`requirements-packaging.txt`**: separate file holding only `pyinstaller>=6.6,<7.0` so PyInstaller's big wheel chain (altgraph, pefile, hooks) doesn't land in `requirements-dev.txt` — release machines install `requirements.txt + requirements-packaging.txt`; day-to-day contributors skip it.
- **`sidecar.spec`** (new, ~170 lines, fully commented): PyInstaller one-folder spec targeting `dist/fintrack-sidecar/`. Datas: `alembic.ini` at bundle root, full `sidecar/db/migrations/` preserved at `sidecar/db/migrations/` inside `_MEIPASS` (env.py + versions/*.py are `exec()`-loaded, can't be analysed statically). `collect_data_files` for yfinance / feedparser / apscheduler (they ship runtime data alongside Python code). `collect_submodules` for uvicorn / apscheduler / sqlalchemy.dialects / alembic / pydantic / pydantic_core / pydantic_settings / fastapi / starlette / anyio (all have plugin-style or string-referenced imports PyInstaller can't trace). Explicit hidden imports for `sidecar.db.{models,base,engine}` + `sidecar.config` (env.py imports them inside a function scope). Excludes pytest / tkinter / IPython / jupyter / notebook. One-folder over one-file because /tmp extraction on every cold launch is 2–10 s on macOS — unacceptable for a user-facing app. `upx=False` (AV false positives), `console=True` (surface errors on stderr).
- **`sidecar/db/migrations_runner.py`**: added `_resource_base()` helper that returns `Path(sys._MEIPASS)` when `sys.frozen` is set, else `Path(__file__).resolve().parents[2]`. Rewires `REPO_ROOT` / `ALEMBIC_INI` / `MIGRATIONS_DIR` to use it. Dev-mode layout and frozen-mode layout are now identical from the runner's perspective.
- **Tauri resource bundling** (`shell/src-tauri/tauri.conf.json`): added `"resources": ["../../dist/fintrack-sidecar"]` to the bundle section. Tauri copies the whole frozen bundle into the `.app`'s resource dir, preserving the `fintrack-sidecar/fintrack-sidecar` + `fintrack-sidecar/_internal/` sibling layout PyInstaller expects. Tauri encodes the `../..` into `_up_/_up_/` segments in the final bundle, but that's transparent when the Rust code uses `app.path().resolve(..., BaseDirectory::Resource)`.
- **Tauri Rust shell** (`shell/src-tauri/src/lib.rs`): new `find_frozen_sidecar(&AppHandle) -> Option<PathBuf>` that tries to resolve `../../dist/fintrack-sidecar/fintrack-sidecar` (or `.exe` on Windows) via `BaseDirectory::Resource` + `is_file()` check; `spawn_sidecar(&AppHandle, port)` prefers the frozen binary when present, falls back to `.venv/bin/python -m sidecar.main` in dev. **No CWD is set for the frozen path** — inheriting whatever the OS provides (launchd's `/` on macOS) means the DB path heuristic in `sidecar/config.py` falls through to `platformdirs.user_data_dir("FinTrack","FinTrack")` cleanly, which is exactly what we want in prod (`~/Library/Application Support/FinTrack/fintrack.db`).
- **End-to-end bundle smoke test** (bundled `FinTrack.app`, 113 MB): launched `Contents/MacOS/shell`, stderr showed `[sidecar] spawning frozen binary: …/Resources/_up_/_up_/dist/fintrack-sidecar/fintrack-sidecar`, `[sidecar] spawned pid … on port 55559`, parent-watchdog started, migrations 0001→0007 ran, `[sidecar] healthy on port 55559` at t=3 s, `/api/health/` returned `{"status":"ok","version":"0.1.0"}`, `/api/assets/` returned the 10 seeded assets with `AAPL` id=1 and `BTC-USD` id=8. DB landed at `~/Library/Application Support/FinTrack/fintrack.db` + WAL shard files. Clean shutdown via kill.
- **Bundle size**: `FinTrack.app` = 113 MB = 10 MB Rust shell + 103 MB frozen Python bundle. First-boot cold start: `.app` → window visible in ~3 s (2 s Tauri health-poll interval + 1 s sidecar boot).

### What was completed (Sprint 5 continued — CI + Release pipeline + updater plugin + docs)
- **`.github/workflows/ci.yml`** (new): runs on every push to main + every PR. Two parallel jobs on ubuntu-latest: `sidecar` (setup-python 3.13 with pip cache keyed on requirements files → install requirements.txt + requirements-dev.txt → ruff check → mypy --strict → pytest -v) and `shell` (pnpm@10 → Node 22 with pnpm cache → install frozen → eslint → vite build). Concurrency group cancels superseded runs.
- **`.github/workflows/release.yml`** (new, 21 steps total): triggers on tag push `v*` + `workflow_dispatch`. Matrix = `macos-latest` + `windows-latest`. Per-runner: checkout → setup-python 3.13 + pip cache → install `requirements.txt + requirements-packaging.txt` → `pyinstaller sidecar.spec --clean --noconfirm` → platform-specific verify-binary-exists step → pnpm@10 + Node 22 + cargo cache → conditional Apple Developer ID cert import into an ephemeral keychain (only when `APPLE_CERTIFICATE` secret is present) → conditional Windows PFX import (only when `WINDOWS_CERTIFICATE` secret is present) → install JS deps → `pnpm -C shell tauri build` with conditional `--config` override to disable updater artifacts when `TAURI_SIGNING_PRIVATE_KEY` is unset (so the first unsigned release can ship without the key set up) → upload primary installer artifacts (`if-no-files-found: error` — build fails if the `.dmg`/`.msi`/`.exe` is missing) → upload updater bundles (`warn` — missing is fine). Release job consolidates all artifacts into a draft GitHub Release via `softprops/action-gh-release@v2`.
- **Tauri updater plugin** (three-part install): `tauri-plugin-updater = "2"` in `src-tauri/Cargo.toml`, `.plugin(tauri_plugin_updater::Builder::new().build())` in `lib.rs` builder chain, `"updater:default"` in `capabilities/default.json`, `@tauri-apps/plugin-updater@2.10.1` in the shell package. `tauri.conf.json` adds `bundle.createUpdaterArtifacts: true` + `plugins.updater.endpoints` pointing at `https://github.com/MasimbaTakudzwa/FinTrack/releases/latest/download/latest.json` + empty `pubkey` placeholder (populated after first `pnpm tauri signer generate`).
- **Local bundle verification**: `pnpm tauri build --bundles app` with updater plugin enabled produces `FinTrack.app` + `FinTrack.app.tar.gz` (51 MB updater bundle) successfully. Signing step errors when `TAURI_SIGNING_PRIVATE_KEY` isn't set — workflow handles this via conditional `--config` override (verified locally: `pnpm tauri build --config '{"bundle":{"createUpdaterArtifacts":false}}'` produces clean `.app` with no signing errors).
- **`docs/development/release_process.md`** (new): end-to-end release runbook. Covers: pipeline overview table (inputs, outputs, artifacts per platform), one-time updater keypair generation (`pnpm tauri signer generate -w ~/.tauri/fintrack.key`), complete GitHub Actions secrets matrix (Apple Developer ID 6 secrets, Windows PFX 2 secrets, updater key 2 secrets, with base64 encoding commands), pre-flight version sync check (tauri.conf.json + Cargo.toml + pyproject.toml), tag-and-push flow, `gh run watch`, draft-release smoke-test checklist (clean-machine install, health indicator, asset page, SQLite DB path, clean shutdown), publish step, rollback procedure (delete release / cut fixed version / instruct users to reinstall), update propagation semantics (GitHub `latest/download/<file>` redirect), troubleshooting (hidden-import failures, xattr "detritus" errors, notarisation hangs, Windows SmartScreen reputation).

### What was completed (Sprint 5 close-out — first v0.1.0 cut + fix)
- **PR #1 merged** (rebase, commits `a78377a` feat packaging + `463190d` docs): CI + Release workflows, updater plugin, release docs all on main.
- **Local gh CLI installed** via `brew install gh` (2.91.0) + `gh auth login`. Added "Local tools you need installed" section to `docs/development/release_process.md` plus browser-URL fallbacks on every `gh` command so future releases can be driven from the GitHub web UI when the CLI isn't handy.
- **v0.1.0 tagged** on `463190d`, pushed. Release workflow triggered (`24800322641`). **macOS build failed** at `pnpm tauri build` with `security: SecKeychainItemImport: One or more parameters passed to a function were not valid. failed to bundle project failed codesign application`. Windows build succeeded — `.msi` + `-setup.exe` uploaded. Release job never ran (matrix partial failure).
- **Root cause identified**: the release workflow's "Build Tauri app" step exported `APPLE_CERTIFICATE` (and every other signing env var) unconditionally from `${{ secrets.APPLE_CERTIFICATE }}`, which GitHub expands to an empty string when the secret is unset. Tauri's macOS bundler treats "env var present" as "sign this build" regardless of payload, so it passed empty bytes to `security import` and died.
- **Fix shipped on PR #2** (`c5ccde3`, `fix(ci): stop Tauri bundler attempting codesign with empty signing secrets`):
  - Signing secrets now ride through the workflow as `_SEC_*`-prefixed env vars; the run script only re-exports them under their canonical names (`APPLE_CERTIFICATE`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID`, `APPLE_CERTIFICATE_PASSWORD`, `WINDOWS_CERTIFICATE`, `WINDOWS_CERTIFICATE_PASSWORD`, `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`) when they actually contain a value. No value → Tauri takes the "no signing configured" path and produces a clean unsigned build.
  - Release job's `files:` glob was pointing at `artifacts/fintrack-macos/**` and `artifacts/fintrack-windows/**` but the matrix uploads as `fintrack-macos-installer` / `fintrack-macos-updater` / `fintrack-windows-installer` / `fintrack-windows-updater`. Fixed to enumerate the four actual directory names — would have left the draft release empty.
  - Added `pnpm -C shell tauri:build:unsigned` script (same `--config '{"bundle":{"createUpdaterArtifacts":false}}'` override the workflow uses) so local packaging tests don't require the updater keypair. Plain `pnpm tauri build` without the key aborts with `A public key has been found, but no private key. Make sure to set TAURI_SIGNING_PRIVATE_KEY environment variable.`
  - Documented both failure modes + the local unsigned-build flow in `docs/development/release_process.md` troubleshooting section.
- **v0.1.0 tag re-pointed**: deleted the old `49fa32a` annotated tag (local + remote), re-tagged as `e69ede9` on the fixed main HEAD (`c5ccde3`). Release workflow re-triggered as run `24801268652`. Watching in background for draft release + artifact verification.

### What was completed (belated Sprint 3 follow-up — Macro page UI)
User flagged during Sprint 5 close-out that `/macro` was still a placeholder — the backend (Sprint 2) had been fully done (API, seeding, FRED fetcher, scheduler job), but the frontend was never built. Closed that gap now.
- **`shell/src/components/MacroLineChart.tsx`** (new, ~105 LOC): Sibling of `CandleChart` — same `lightweight-charts` v5 lifecycle (createChart once + cleanup on unmount, setData on points change), uses `LineSeries` because FRED observations are single-valued. Light/dark palettes match the rest of the app. `toLineData` coerces `"YYYY-MM-DD"` → `UTCTimestamp` via `Date.parse(\`${p.date}T00:00:00Z\`) / 1000` so the time-scale always treats the data as UTC midnight regardless of the user's local offset.
- **`shell/src/pages/Macro.tsx`** (rewritten, ~360 LOC, replaces the `PagePlaceholder` stub): Two-column layout.
  - **Left (`IndicatorList`)**: sorted-alphabetical list of FRED series as clickable buttons; active state gets indigo tint + bolder label. Each row shows series_id (monospace) + name + frequency·units caption.
  - **Right (`SeriesPanel`)**: header card with series name + series_id/frequency/units meta + observation count + description. Below that, the chart in its own panel, then a 4-cell `StatsGrid` (Latest / Previous / vs. previous / vs. series-start date) with up/down/neutral tone colouring + ArrowUp/DownRight icons. `summarise()` computes latest/previous/earliest points + both percent-change windows, guards against divide-by-zero.
  - **`NoDataHint`**: shown when a series has zero observations (the realistic default without a FRED API key). LineChart icon + friendly copy + indigo CTA button linking to `/settings` where the user can paste their FRED key.
- **React 19 hook rule compliance**: both effects only call `setState` from `.then`/`.catch` handlers — never inline in the effect body. Two effects: one for the indicator list (bumps on `tick`), one for the selected series (bumps on `selected` + `tick`). Refresh button bumps `tick` to re-run both.
- **Formatting helpers**: `fmtValue(n, units)` detects Percent units (`/percent|%/i.test(units)`) and appends `%` — so CPIAUCSL shows `"315.60"` while UNRATE shows `"3.80%"`. `fmtDate(iso)` parses `"YYYY-MM-DD"` as UTC midnight (timezone-safe). `fmtPct` handles the relative-change display with sign.
- **Dark mode**: `useResolvedTheme()` → palette toggle in the chart; Tailwind `dark:` variants everywhere else.
- **Verifications**: `pnpm -C shell lint` clean, `pnpm -C shell build` clean — bundle went from 557 kB / 172 kB gzipped (Sprint 4 end) to **569 kB / 175 kB gzipped** (+12 kB raw / +3 kB gzipped for MacroLineChart + Macro page). No new dependencies — uses the existing `lightweight-charts`, `lucide-react`, `react-router-dom`.
- **Shipped as PR #3** (`645626e`, rebase-merged on main) after CI both jobs green (Shell 20 s, Sidecar 47 s).

### What was completed (README refresh — PR #4, `8c6c7b6`)
The top-level `README.md` had been stuck on Sprint 1 state since project kickoff — described the active sprint as Sprint 1, pointed at a non-existent `docs/development/setup.md`, and had no user-facing install instructions. Refreshed for Phase 1 completion:
- **"What it does"** section enumerates every shipping feature (Dashboard + sparklines, Asset detail with timeframes + measurement tool, Watchlists with drag-reorder + default-pinning, News with symbol filter + day grouping, Macro with FRED indicators + stats, Price alerts with desktop notifications, Market overview with top gainers/losers, Settings with theme + runtime config).
- **Installation** section with per-platform downloads (`.dmg` / `.msi` / `-setup.exe`), the unsigned-build right-click step for first macOS launch, and OS app-data paths for the SQLite DB.
- **Privacy** section spells out the data-flow invariants: outbound only to Yahoo Finance / CoinGecko / FRED, no telemetry, sidecar bound to `127.0.0.1` only, DB file is user-owned.
- **Development** section rewritten with actually-working commands, `FINTRACK_EXTERNAL_SIDECAR` dev-mode escape hatch, and a pointer to `docs/development/release_process.md`.
- **Project layout** tree updated to match reality (services/, sidecar.spec, requirements-packaging.txt).
- Verifications: both LICENSE and `docs/development/release_process.md` links resolve. CI both jobs green on PR #4 (Shell 20 s, Sidecar 51 s).

### Housekeeping sweep (same session)
- Scanned `DECISIONS.md` — all 13 decisions are "resolved" or "superseded"; no open items to act on.
- Scanned source tree for `TODO`/`FIXME`/`XXX`/`HACK` — none. Codebase is clean.
- Bundle identifier `com.fintrack.app` is technically malformed (ends in `.app`) — flagged as a v0.1.1 follow-up. Not changing now because v0.1.0 draft release is already built with the current identifier and changing mid-tag would require re-tagging + re-uploading. Defer to the next tag cut.

### What was completed (PR #6 — macro fire-on-first-add + chunked bulk insert, commit `0f88e43`)
User pasted their FRED key into the bundled `.app`'s Settings UI and asked "Are we not able to implement the macros without shipping the app?". Two latent bugs surfaced in the same turn:

- **Fire-on-first-add semantics in `sidecar/scheduler/__init__.py`**: `ingest_macro` was cron-only (`CronTrigger(hour=6)`) without `next_run_time`, so a fresh FRED key triggered no backfill until 06:00 UTC of the next day — up to 24 h of nothing. Fix: pass `next_run_time=now` *only* when `scheduler.get_job("ingest_macro") is None` (first-add detection), so sidecar restarts and cron-hour reconfigures don't re-fire. Matches the intent "show the user macro data within seconds of pasting their key" without breaking the intent "honour the scheduled cron hour on subsequent runs".
- **Chunked bulk insert in `sidecar/scheduler/jobs.py`**: the first manual trigger against the user's DB aborted with `sqlite3.OperationalError: too many SQL variables`. Root cause: `ingest_macro` built a single `INSERT ... ON CONFLICT DO NOTHING` statement with ~19k rows × 3 columns = ~57k bind params, blowing past SQLite's 32766-variable cap. Latent because the cron had never actually fired and tried to insert a multi-decade payload. Fix: loop the insert 500 rows at a time (~1500 params per statement).
- **Tests**: `test_register_jobs_fires_macro_immediately_on_first_add_with_fred_key` + `test_register_jobs_does_not_refire_macro_on_reconfigure` cover both sides of the first-add conditional; `test_ingest_macro_chunks_large_backfills` exercises the chunk loop with a 1250-row fake payload.
- **Live verification**: ran `ingest_macro()` manually against the user's `~/Library/Application Support/FinTrack/fintrack.db` with their real FRED key → **19,125 observations landed**: CPIAUCSL 950 (1947→2026 monthly), UNRATE 938, FEDFUNDS 861, **DGS10 16,060** (daily from 1962 — this series was the chunk-killer), GDP 316. The Macro page in their already-running `.app` populates on next refresh without any rebuild.
- Verifications: `pytest` 244/244 green (+3 new tests over 241), `ruff check .` clean, `mypy --strict sidecar/` clean on 38 files, CI green on Shell (lint + build) and Sidecar (lint + types + tests). PR #6 rebase-merged as `0f88e43`.

### What to work on NEXT (in order)
1. [ ] **Confirm the re-triggered release workflow produces a draft release** with all four artifacts attached (`.dmg`, `.msi`, `-setup.exe`, and any updater bundles that happen to exist). If the macOS build still fails, read the step log and iterate — next likely culprits are (a) yfinance/feedparser data-file inclusion in the PyInstaller spec on the runner's Python 3.13 vs. local 3.13, (b) xattr "resource fork" errors on the frozen sidecar (document already mentions `xattr -cr dist/fintrack-sidecar/` as the fix).
2. [ ] **Smoke-test the draft release artifacts** on a clean Mac (install from `.dmg`, confirm health indicator goes green, confirm SQLite lands at `~/Library/Application Support/FinTrack/fintrack.db`, exercise a watchlist + an alert + add-asset, clean-quit via Cmd+Q → verify no orphan `fintrack-sidecar` process). Skip Windows smoke-test unless a Windows box is available.
3. [ ] **Live GUI smoke pass** for Sprint 4 features against the bundled `.app` (the unbundled dev-mode works — this catches packaging-specific regressions): Dashboard "Add asset" → lookup `TSLA` / `GME` / `BTC-USD` → bars appear within ~1 min. `/watchlists` rename / set default / delete non-default (confirm dialog fires — `window.confirm` is suppressed in WKWebView). Market overview Top losers populates. `/alerts` delete via dialog. AssetDetail timeframe toggle + measurement tool + create-alert → wait ≤1 min → header bell badges + OS notification shows "FinTrack" (not "Terminal").
4. [ ] **Promote draft to published** via `gh release edit v0.1.0 --draft=false` after smoke-test passes. Update README with installation instructions + download link.
5. [ ] **Sign-up follow-ups** (deferred from Sprint 5, tracked for a future v0.1.1): generate updater keypair + populate `pubkey` in `tauri.conf.json` + add `TAURI_SIGNING_PRIVATE_KEY` secret. Apple Developer ID ($99/yr) + cert import → add Apple 6 secrets. Windows EV cert or Azure Trusted Signing → add Windows 2 secrets. All documented in `docs/development/release_process.md`.

### Active blockers
- None

### Session notes (additions from Sprint 2)
- **SQLite `too many SQL variables` cap**: SQLite defaults to a 32766-variable limit per compiled statement (was 999 in pre-3.32 builds). For `sqlite_insert(Table).values(rows)`-style bulk upserts, the budget is `len(rows) × len(columns)`. `ingest_macro` blew through it on a first FRED backfill (19k rows × 3 cols = 57k params). Rule of thumb for any bulk insert that could plausibly hit thousands of rows: chunk at 500 rows per statement in a loop — gives headroom regardless of column count, and `ON CONFLICT DO NOTHING` semantics are preserved because each statement is an independent upsert. Aggregate `rowcount` across chunks for the return value.
- **APScheduler cron-only jobs need fire-on-first-add when the triggering config is user-supplied at runtime**: `CronTrigger(hour=6)` alone means the first fire is "tomorrow at 06:00 UTC" (or today if current time < 06:00 UTC). Fine for jobs with defaults at install time, terrible for jobs gated on a user-entered secret (FRED key) where "paste key → wait 24 h → see data" is unshippable UX. Pattern: at `_register_jobs` time, check `scheduler.get_job(job_id) is None`. True → first-add → pass `next_run_time=datetime.now(UTC)`. False → leave it out, so APScheduler uses `trigger.get_next_fire_time(None, now)` for the cron's natural schedule. Works with `replace_existing=True` because APScheduler computes a fresh next_run_time from the trigger whenever `next_run_time` isn't passed explicitly.
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
- **APScheduler `replace_existing` needs a started scheduler**: on an unstarted BackgroundScheduler, `add_job` stages jobs in a pending list and `get_job` reads from the jobstore — so `add_job(replace_existing=True)` appears to no-op when verifying in tests. Fix: call `scheduler.start(paused=True)` in the test fixture so jobs flush to the jobstore without the worker thread firing. Production code is unaffected (always calls `start()`).
- **APScheduler `IntervalTrigger` first fire defaults to `now + interval`, NOT now**: cold-starting a sidecar with a 5-min interval job leaves the dashboard empty for 5 minutes. Pass `next_run_time=datetime.now(UTC)` to `add_job` to get an immediate first fire. Only apply to interval jobs — cron jobs should honour their wall-clock schedule.
- **Settings precedence rule**: DB row > env var > hardcoded default. Set via env vars you want pinned for dev/tests/CI; once a user writes via the UI, the DB row takes over. `FINTRACK_ENABLE_SCHEDULER`/`FINTRACK_ENABLE_SEED` deliberately stay env-only (kill switches), as do `FINTRACK_PORT`/`FINTRACK_DB_PATH`/`LOG_LEVEL` (runtime-required or restart-required).
- **`ingest_macro` reads FRED key lazily**: job calls `load_effective_config()` on each invocation so `/api/config/` updates take effect without a sidecar restart. The scheduler's add-or-remove decision for the macro job itself also uses effective config (via `_register_jobs`).
- **feedparser typed stubs**: `feedparser` ≥ 6.0.12 ships its own type hints. Do NOT add `# type: ignore[import-untyped]` — mypy `--strict` will flag it as an unused ignore.
- **`datetime(*struct_time[:6], tzinfo=UTC)` fails mypy**: unpack shape confuses the type checker into thinking `tzinfo` gets multiple values. Unpack explicitly: `datetime(val[0], val[1], val[2], val[3], val[4], val[5], tzinfo=UTC)` and include `IndexError` in the except tuple for short struct_times.
- **Upsert-then-SELECT for SQLite without RETURNING**: to dedup `articles` by URL and hydrate IDs for both new + existing rows, run `INSERT ... ON CONFLICT(url) DO NOTHING` then `SELECT url, id WHERE url IN (...)` in the same transaction. Works for the composite-PK link table too — `ON CONFLICT(article_id, asset_id) DO NOTHING`.
- **Yahoo RSS feed is unofficial** like yfinance. Shape observed on 2026-04-22: 20 items per symbol, `<title>/<link>/<pubDate>/<description>`, channel title `"Yahoo Finance - {SYMBOL}"`. Per-entry `source` field is rarely populated, so the fetcher falls back to the literal `"Yahoo Finance"`. Keep fetcher source-swappable against future Yahoo breakage.
- **`react-hooks/set-state-in-effect` (React 19)**: this ESLint config's hooks plugin errors on any synchronous `setState` call inside a `useEffect` body — including `setLoading(true)` before an `await`, and even before returning a cleanup fn. Fix patterns: (a) call `setState` only from `.then`/`.catch` handlers or post-`await` in an async IIFE (state transitions happen inside handlers, not inline); (b) on prop change where you want a fresh initial state, add `key={someDep}` to remount the component; (c) for user-triggered "show loading" feedback (Refresh, filter dropdown), put the `setState` in the event handler — event handlers are NOT governed by the rule.
- **Partial unique index in SQLite via Alembic**: use `op.create_index(name, table, [col], unique=True, sqlite_where=sa.text("col = 1"))`. This gave us "at most one default watchlist" as a DB invariant. Service code still has to demote existing defaults BEFORE promoting a new one in the same transaction — otherwise the partial-unique index fires mid-transaction. Defence-in-depth: a test writes two `is_default=True` rows directly via `session.flush()` and asserts `IntegrityError`.
- **Ruff N818 exception naming**: exception class names MUST end with `Error` (e.g. `WatchlistNotFoundError`, not `WatchlistNotFound`). Applies to everything — domain errors, HTTP-level errors, everything. Ruff catches it; rename before it bites in review.
- **`@dnd-kit` sortable essentials**: wrap the list in `<DndContext sensors={[PointerSensor, KeyboardSensor w/ sortableKeyboardCoordinates]} collisionDetection={closestCenter} onDragEnd={...}>` + `<SortableContext items={ids} strategy={verticalListSortingStrategy}>`. Each row uses `useSortable({ id })` → spread `attributes` + `listeners` on the drag handle only (not the whole row), apply `transform: CSS.Transform.toString(transform)` + `transition` to the row's inline style. `items` must be primitive IDs (numbers or strings), not objects.
- **Optimistic UI reorder pattern**: commit the new order to React state immediately, then fire the `reorderWatchlistItems()` PUT. On success, do nothing (state already matches server). On failure, revert state to the captured-before-call snapshot AND surface an error message. Keeps drag-drop responsive without dropping the user into a loading spinner on every swap.
- **SQLEnum with lowercase values**: SQLAlchemy's `Enum(PyEnum)` defaults to storing the Python **name** (e.g. `"ABOVE"`). To persist the enum **value** (e.g. `"above"`), pass `values_callable=lambda e: [m.value for m in e]`. Also name the SQLite ENUM check constraint explicitly (`name="alert_direction_enum"`) so Alembic migrations don't produce anonymous constraint names that collide across migrations.
- **SQLite tz-stripping shows up in idempotency assertions**: service calls that write a tz-aware `datetime` and return the object in the same session have the original tz intact; the NEXT call that reads the row back gets a naive datetime (SQLite stores no offset). When asserting "stamp didn't move," strip tz on both sides before comparing: `a.replace(tzinfo=None) == b.replace(tzinfo=None)`. The proper long-term fix is a TypeDecorator that re-attaches UTC on read — deferred.
- **Tauri notification plugin — three-part install**: (1) `tauri-plugin-notification = "2"` in `src-tauri/Cargo.toml`; (2) `.plugin(tauri_plugin_notification::init())` in `lib.rs` builder chain; (3) `"notification:default"` in `capabilities/default.json` permissions. Also install `@tauri-apps/plugin-notification` on the JS side. Missing any one = silent no-op at runtime.
- **One-shot alert semantics via two-timestamp handshake**: stamping `triggered_at` means "crossed the threshold" (scheduler-side); stamping `notified_at` means "we actually showed the OS notification" (shell-side). A shell crash between these two steps replays on the next poll — at worst a duplicate ping, never a lost alert. `reset=true` on PUT clears both so the alert re-arms. Simpler + more resilient than an SSE event bus for a local-only app.
- **Pydantic v2 `model_fields_set` is the idiomatic way to distinguish "omitted" from "explicit null"** in PATCH-style endpoints: e.g. `sent_note = "note" in body.model_fields_set`. Pair with an `update_note: bool = False` flag on the service layer instead of a sentinel — mypy handles `bool | None` cleanly but chokes on `SomeClass | None | _UnsetType` with `Ellipsis` defaults.
- **`model_config = {"extra": "forbid"}`** on a Pydantic update-request model makes FastAPI return 422 for unknown keys — catches "user tried to edit asset_id via the update route" bugs cleanly. Applied to `UpdateAlertIn` (matches how the client always omits immutable fields, so a 422 means genuine client error).
- **`window.confirm()` is silently suppressed in the Tauri v2 WKWebView on macOS.** Any click that routes through `confirm()` just no-ops — no dialog, no error, just nothing. Use an in-app modal instead (we have `ConfirmDialog`). Applies to `window.alert()` and `window.prompt()` too. Note for future: don't reach for native dialog APIs from the React side in Tauri apps.
- **yfinance `Ticker.fast_info` is preferred for symbol resolution**: ~100-200× faster than `.info` (returns immediately vs. 1-3s HTTP request for info). Carries `quote_type`, `currency`, `exchange`, `last_price`, `timezone`. For display name (`longName`/`shortName`) you still need `.info`. Handle both as `getattr`-with-fallback because the shape varies by quote type (e.g., FX pairs lack `exchange`, futures lack `currency` on some tickers).
- **yfinance `quoteType` enum** maps to our AssetType: `EQUITY`→stock, `ETF`→etf, `CRYPTOCURRENCY`→crypto, `INDEX`→index, `FUTURE`/`COMMODITY`→commodity, `MUTUALFUND`→etf (close enough for display), `CURRENCY`→commodity (FX pair — no better bucket today). Unknown/unmapped → default to stock.
- **Post-commit side-effects pattern**: when a service function both persists AND triggers external work (like kicking off a one-shot ingest after `add_asset`), start the side-effect *outside* the `session_scope()` context manager. Otherwise the side-effect runs against an uncommitted transaction and breaks in subtle ways (e.g. the scheduler job reads an empty `assets` table because this session hasn't flushed yet). The commit must happen first.
- **lightweight-charts v5 click-to-measure**: `chart.subscribeClick(handler)` fires `MouseEventParams` with `.time` (UTCTimestamp) + `.point` (screen coords). Convert to price with `candleSeries.coordinateToPrice(param.point.y)` — returns `number | null`. For markers, call `createSeriesMarkers(candle, [])` once (returns an `ISeriesMarkersPluginApi<Time>`), then `.setMarkers([{time, position: "inBar", shape: "circle", color, text: "A"}, ...])` whenever the measurement changes. **Gotcha**: the click callback in `useRef` must be updated in a separate effect from the subscribe effect — otherwise a new chart is created on every callback change. Pattern: subscribe once, read from a `callbackRef.current` inside the handler.
- **Tauri dev-mode notification attribution**: in `pnpm tauri dev`, notifications are attributed to whatever spawned the Tauri binary (often "Terminal" on macOS, "iTerm" in iTerm2, etc.) because the unbundled `target/debug/shell` has `CFBundleIdentifier=NULL`. In bundled `.app` mode with `identifier` set in `tauri.conf.json`, macOS correctly shows "FinTrack". Not a bug — expected. The in-app `NotificationCenter` in the Header is the canonical surface anyway; it gives persistent history, works without OS permission, and sidesteps attribution issues.
- **`useRef<T | null>(null)` for mutable callback refs beats putting the callback in the effect deps**: when a subscribe effect must not re-run on every render but you also need the latest handler, store the handler in a separate `useEffect(() => { ref.current = handler; })`. The subscribe effect reads `ref.current` at fire time. Prevents expensive tear-down/re-setup on every keystroke.
- **Tauri v2 `resources` glob vs plain-directory semantics**: `{"glob/**/*": "dest/"}` in `tauri.conf.json` **FLATTENS** the entire tree — every matched file is placed directly under `{dest}/`, structure lost. For PyInstaller one-folder bundles where `_internal/` MUST stay a sibling of the binary, use a **plain directory path** as an array element: `"resources": ["../../dist/fintrack-sidecar"]`. Tauri recurses with structure preserved. Verified by inspecting `FinTrack.app/Contents/Resources/` post-build: plain path = 2 entries (binary + `_internal/`); glob = 878 flattened files with timezone names at the top level.
- **Tauri v2 `_up_` escape sequence for `../` in resource paths**: when a `resources` entry uses `..` to escape out of `src-tauri/`, Tauri replaces each `..` with a `_up_` segment in the bundled path. So `../../dist/fintrack-sidecar` lands at `Contents/Resources/_up_/_up_/dist/fintrack-sidecar/`. **Never hard-code the `_up_`-decorated path** — use `app.path().resolve(orig_path, BaseDirectory::Resource)` which applies the same encoding internally. One code path works in dev (raw `src-tauri/` cwd → resolved path doesn't exist → `is_file()` fails → fall back) and prod (bundled → resolved path exists → spawn frozen binary).
- **PyInstaller one-folder `sys._MEIPASS` in frozen mode**: when `sys.frozen` is true, `sys._MEIPASS` points to the `_internal/` sibling dir in one-folder mode (and a temp extraction dir in one-file mode — which we don't use). Migration runner treats `_MEIPASS` as the equivalent of the dev-mode repo root: `alembic.ini` at `_MEIPASS/alembic.ini`, migrations at `_MEIPASS/sidecar/db/migrations/`. Same layout on both paths, one implementation.
- **PyInstaller `collect_submodules` + `collect_data_files` are essential for plugin-style packages**: anything that imports submodules by string (uvicorn loops/protocols, apscheduler triggers/executors, sqlalchemy.dialects, alembic, pydantic internals) needs `collect_submodules`. Anything that ships runtime data (yfinance pickled constants, feedparser encoding map, apscheduler entry_points metadata) needs `collect_data_files`. Without these, the frozen binary imports cleanly but fails at runtime when something does `importlib.import_module("uvicorn.loops.auto")` and gets `ModuleNotFoundError`.
- **Alembic migrations in frozen bundles MUST be bundled as data files**, not hidden imports: env.py + versions/*.py are `exec()`-loaded by alembic, so PyInstaller's static AST traversal never sees them. Bundle the whole `sidecar/db/migrations/` dir via `datas=[(src, "sidecar/db/migrations")]` in the spec.
- **yfinance/anyio/greenlet "missing module" warnings are mostly noise**: the `build/sidecar/warn-sidecar.txt` file lists every optional import PyInstaller couldn't trace — including `sniffio`, `exceptiongroup`, `greenlet`, `orjson`, `psutil`, etc. None of these are actually installed or required with Python 3.13 + asyncio (anyio's asyncio backend is self-contained, ExceptionGroup is built-in since 3.11). Don't chase them unless the frozen binary genuinely fails at runtime — debug by running the frozen binary with `PYTHONUNBUFFERED=1` and reading stderr.
- **PyInstaller frozen binary "silent hang" is usually a debugging artefact, not a real hang**: first attempt to run the bundled binary returned zero stdout, leading to a multi-hour wild-goose-chase. Root cause: earlier orphaned `fintrack-sidecar` processes were holding the test port, causing subsequent launches to exit before printing anything. Fix: `pgrep -f fintrack-sidecar | xargs kill -9` before each test run, use a fresh port each time, always invoke with `PYTHONUNBUFFERED=1`. The binary actually boots in ~1 s on Mac.
- **Bundled `.app` cold-start path on macOS**: `FinTrack.app/Contents/MacOS/shell` (Tauri binary) → spawns `Contents/Resources/_up_/_up_/dist/fintrack-sidecar/fintrack-sidecar` (PyInstaller bootloader) → the bootloader loads `_internal/Python` framework + archive → runs `sidecar.main` → uvicorn binds port → Tauri poller hits `/api/health/` after ~2–3 s, window visible. No user-visible delay.
- **Tauri updater — three-part install + config**: Cargo dep (`tauri-plugin-updater`), plugin registration in `lib.rs` builder, `"updater:default"` in capabilities, JS package (`@tauri-apps/plugin-updater`), AND `plugins.updater.endpoints` + `plugins.updater.pubkey` in `tauri.conf.json`. The `pubkey` field is NOT optional — even an empty string causes the bundler to assume updater signing is wanted. Combined with `bundle.createUpdaterArtifacts: true`, this means the build hard-fails with "A public key has been found, but no private key" when `TAURI_SIGNING_PRIVATE_KEY` env var is unset.
- **Conditional updater artifacts in CI**: the release workflow uses `shell: bash` + an `if [ -n "$TAURI_SIGNING_PRIVATE_KEY" ]` check to branch between `pnpm tauri build` (with updater) and `pnpm tauri build --config '{"bundle":{"createUpdaterArtifacts":false}}'` (without). Lets the first unsigned release ship before the updater keypair has been generated + added as a GH secret. Works on windows-latest because Git Bash ships by default and `shell: bash` uses it.
- **Apple Developer ID ephemeral keychain pattern**: in CI, base64-decode the `.p12` from `APPLE_CERTIFICATE` secret, create a temporary keychain at `$RUNNER_TEMP/build.keychain` with a random password, `security import` the cert with `-T /usr/bin/codesign -T /usr/bin/security`, `security set-key-partition-list -S apple-tool:,apple:` to allow non-interactive access, then set it as the default keychain. No persistent state on the runner. Same pattern works for mac release signing AND future app-notarisation flows (notarytool reads the same identity).
- **`if-no-files-found: error` vs `warn`** in GH Actions `upload-artifact@v4`: use `error` for required outputs (installers — a missing `.dmg` means the build silently broke) and `warn` for optional outputs (updater bundles — only produced when signing is set up). The job passes with just warnings, so unsigned releases work end-to-end.
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
- [x] Settings page: theme selector (client), data-source toggles, refresh intervals, FRED key, read-only runtime info; full backend w/ settings table + /api/config GET/PUT + scheduler reconfigure (3F)
- [x] Macro page: indicator list sidebar + series detail panel with `MacroLineChart` (lightweight-charts `LineSeries`) + stats grid (latest / previous / vs-previous / vs-start) + `NoDataHint` CTA when FRED key unset (belated follow-up; shipped during Sprint 5 close-out)

---

### Sprint 4 — News, Watchlists & Desktop Alerts
**Goal:** News aggregation, local watchlists, desktop-native price alerts
**Scope:** Phase 1

#### Milestone 4A — News ingestion pipeline (backend)
- [x] `Article` model + `article_assets` association table in `sidecar/db/models.py`
- [x] Alembic migration `0005_create_articles.py`
- [x] `sidecar/ingestion/rss_fetcher.py` — Yahoo Finance RSS via `feedparser`, per-symbol, exponential backoff + timeout
- [x] `ingest_news` scheduler job (polls every active asset's RSS feed, dedups on `url`, associates to asset(s))
- [x] New settings keys: `ingest_news.enabled` (bool, default true), `ingest_news.interval_minutes` (int, default 15)
- [x] `GET /api/news/?symbol=&from=&to=&limit=` — list articles newest-first, optional symbol filter
- [x] Tests: fetcher (mocked `_http_get`), job upsert/idempotency, API filter + 404 on unknown symbol, scheduler reconfigure

#### Milestone 4B — News UI
- [x] Wire real data into the AssetDetail "Recent news" sidebar (replace Sprint 3 placeholder)
- [x] New `/news` standalone page with symbol filter dropdown + time-grouped list (Today / Yesterday / date)
- [x] Sidebar nav entry (`Newspaper` icon between Market and Macro)
- [x] Empty / error / loading states consistent with Dashboard
- [x] URL-backed filter state (`?symbol=AAPL`) so AssetDetail "See all" deep-link works

#### Milestone 4C — Watchlists
- [x] `Watchlist` + `WatchlistItem` models (single-user, no user_id — `position` int for drag-reorder)
- [x] Alembic migration `0006_create_watchlists.py` with partial unique index `ux_watchlists_default_one`
- [x] Seed a "Default" watchlist with all active assets on first migration (+ backfill on subsequent runs)
- [x] CRUD endpoints: `GET/POST /api/watchlists/`, `GET/PUT/DELETE /api/watchlists/{id}/`, `POST/DELETE /api/watchlists/{id}/items/`, `PUT /api/watchlists/{id}/items/reorder`
- [x] UI: `/watchlists` page with list-of-lists + item management + drag-reorder (`@dnd-kit`) + optimistic reorder w/ revert-on-failure
- [x] Dashboard pivots to read from default watchlist instead of "all active assets"

#### Milestone 4D — Price alerts + desktop notifications
- [x] `PriceAlert` model (asset_id FK, threshold Decimal, direction enum above/below, is_active, triggered_at nullable, notified_at nullable, note)
- [x] Alembic migration `0007_create_price_alerts.py` (three indexes for scheduler scan paths + CASCADE FK)
- [x] `check_price_alerts` scheduler job (default 1-min interval, reads latest PricePoint per active alert, stamps `triggered_at` when crossed inclusively)
- [x] Tauri `notification` plugin wired + permission request on first poll tick
- [x] Sidecar→shell bridge for notification delivery — polling handshake (shell polls `/api/alerts/pending-notifications/`, fires OS notification, POSTs `/mark-notified/`)
- [x] CRUD endpoints: `GET/POST /api/alerts/`, `GET/PUT/DELETE /api/alerts/{id}/`, plus `/pending-notifications/` + `/{id}/mark-notified/`
- [x] Alert-create modal from AssetDetail ("Create alert" button → direction toggle + threshold prefilled with last close + optional note)
- [x] Alert-history UI page `/alerts` with filter tabs (all/active/triggered), status chips, Pause/Resume/Reset/Delete actions

---

### Sprint 5 — Packaging & Distribution
**Goal:** Signed installers for Mac and Windows, auto-updater, release pipeline
**Scope:** Phase 1 close

#### Tasks (refine at Sprint 5 start)
- [x] Python sidecar frozen with PyInstaller (one-folder mode) — verify SQLite + yfinance + APScheduler work in frozen bundle
- [x] Tauri config bundles frozen sidecar (via `resources` plain-directory path; `external_bin` not used — `app.path().resolve(BaseDirectory::Resource)` instead)
- [x] GitHub Actions matrix: `macos-latest` + `windows-latest`
- [x] Mac code signing + notarisation hooks (Apple Developer ID — pipeline ready, no-op when secrets unset)
- [x] Windows signing hooks (EV cert OR Azure Trusted Signing — pipeline ready, no-op when secrets unset)
- [x] Tauri updater plugin configured (GitHub Releases as update feed; pubkey placeholder; conditional `createUpdaterArtifacts` in CI)
- [x] First release: v0.1.0 tagged, `.dmg` + `.msi` + `-setup.exe` attached to draft GitHub Release (one click from publish)
- [x] `docs/development/release_process.md`

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
