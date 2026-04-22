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

**Last updated:** 2026-04-22 — Session 003 (checkpoint 11 — Sprint 4C complete: Watchlists backend + UI + Dashboard pivot)
**Active sprint:** Sprint 4 — News, Watchlists & Desktop Alerts (4A ✅, 4B ✅, 4C ✅, 4D next)
**Overall status:** 🟢 Sprints 1, 2 & 3 complete; Sprint 4A (news ingestion) + 4B (News UI) + 4C (Watchlists) complete — Dashboard now reads from the default watchlist, `/watchlists` page lets the user CRUD + drag-reorder

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

### What to work on NEXT (in order)
1. [x] **Sprint 4A — News ingestion pipeline (backend)** — Article/ArticleAsset models, migration 0005, Yahoo RSS fetcher, ingest_news job, config + scheduler wiring, `/api/news/` endpoint, full test coverage, live smoke test with 162 articles.
2. [x] **Sprint 4B — News UI** — listNews() client helper, reusable NewsList component, AssetDetail sidebar integration, standalone /news page with URL-backed symbol filter, sidebar nav entry.
3. [x] **Sprint 4C — Watchlists** — models, migration 0006 with partial unique index, service + API + 51 tests, `/watchlists` page with drag-reorder, Dashboard pivot, routing + nav. Done this session.
4. [ ] **Live GUI smoke for 4C** — run `pnpm tauri dev`, open `/watchlists`, create a watchlist, add a couple of assets, drag-reorder them, promote it to default, go back to Dashboard and verify the order matches. Delete a non-default list; try to delete the default (should be blocked with a 409 toast). Rename a list. Verify the Dashboard empty-state CTA shows when the default watchlist has no items.
5. [ ] **Sprint 4D — Price alerts + desktop notifications**: `PriceAlert` model, `check_price_alerts` job (5-min interval, reads latest `PricePoint` for each active alert), Tauri `notification` plugin wired to a sidecar→shell event bridge, alert-history UI page, alert-create modal from AssetDetail.

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
- [ ] `PriceAlert` model (asset_id FK, threshold Decimal, direction enum above/below, is_active, triggered_at nullable)
- [ ] Alembic migration `0007_create_price_alerts.py`
- [ ] `check_price_alerts` scheduler job (5-min interval, reads latest PricePoint per active alert, flips `triggered_at` when crossed)
- [ ] Tauri `notification` plugin wired + permission request on first alert-fire
- [ ] Sidecar→shell bridge for notification delivery (options: SSE endpoint consumed by shell, or Tauri event from a polling shell-side watcher — pick simplest)
- [ ] CRUD endpoints: `GET/POST /api/alerts/`, `PUT/DELETE /api/alerts/{id}/`
- [ ] Alert-create modal from AssetDetail ("alert me when AAPL > $250")
- [ ] Alert-history UI page with triggered-state indicator

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
