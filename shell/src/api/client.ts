import { invoke } from "@tauri-apps/api/core";

let baseUrlPromise: Promise<string> | null = null;

export async function getBaseUrl(): Promise<string> {
  if (!baseUrlPromise) {
    baseUrlPromise = (async () => {
      const port = await invoke<number>("get_sidecar_port");
      if (!port) {
        throw new Error("Sidecar port is 0 — shell did not spawn the sidecar");
      }
      return `http://127.0.0.1:${port}`;
    })();
  }
  return baseUrlPromise;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly url: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type QueryValue = string | number | boolean | null | undefined;

function buildQuery(params?: Record<string, QueryValue>): string {
  if (!params) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined) continue;
    usp.set(k, String(v));
  }
  const qs = usp.toString();
  return qs ? `?${qs}` : "";
}

async function apiGet<T>(
  path: string,
  opts: { params?: Record<string, QueryValue>; signal?: AbortSignal } = {},
): Promise<T> {
  const base = await getBaseUrl();
  const url = `${base}${path}${buildQuery(opts.params)}`;
  const res = await fetch(url, { signal: opts.signal });
  if (!res.ok) {
    throw new ApiError(res.status, url, `GET ${path} → HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

async function apiPut<T, B>(
  path: string,
  body: B,
  opts: { signal?: AbortSignal } = {},
): Promise<T> {
  return apiJson<T, B>("PUT", path, body, opts);
}

async function apiPost<T, B>(
  path: string,
  body: B,
  opts: { signal?: AbortSignal } = {},
): Promise<T> {
  return apiJson<T, B>("POST", path, body, opts);
}

async function apiJson<T, B>(
  method: "POST" | "PUT" | "DELETE",
  path: string,
  body: B,
  opts: { signal?: AbortSignal },
): Promise<T> {
  const base = await getBaseUrl();
  const url = `${base}${path}`;
  // DELETE goes without a JSON body — typical REST convention. POST/PUT
  // send the JSON payload as-is.
  const init: RequestInit = {
    method,
    signal: opts.signal,
  };
  if (method !== "DELETE") {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    throw new ApiError(res.status, url, `${method} ${path} → ${await _detail(res, method, path)}`);
  }
  // 204 No Content has an empty body.
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function apiDelete(
  path: string,
  opts: { signal?: AbortSignal } = {},
): Promise<void> {
  const base = await getBaseUrl();
  const url = `${base}${path}`;
  const res = await fetch(url, {
    method: "DELETE",
    signal: opts.signal,
  });
  if (!res.ok) {
    throw new ApiError(res.status, url, `DELETE ${path} → ${await _detail(res, "DELETE", path)}`);
  }
}

async function _detail(
  res: Response,
  method: string,
  path: string,
): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // non-JSON or empty response
  }
  return `${method} ${path} HTTP ${res.status}`;
}

// ---------- Health ----------

export interface HealthResponse {
  status: string;
  version: string;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiGet<HealthResponse>("/api/health/", { signal });
}

// ---------- Assets ----------

export type AssetType =
  | "stock"
  | "etf"
  | "crypto"
  | "commodity"
  | "index";

export interface Asset {
  id: number;
  symbol: string;
  name: string;
  asset_type: AssetType;
  is_active: boolean;
  created_at: string; // ISO 8601 — treat as UTC
}

export function listAssets(
  opts: { activeOnly?: boolean; signal?: AbortSignal } = {},
): Promise<Asset[]> {
  return apiGet<Asset[]>("/api/assets/", {
    params: { active_only: opts.activeOnly ?? true },
    signal: opts.signal,
  });
}

export interface AssetLookup {
  symbol: string;
  name: string;
  asset_type: AssetType;
  exchange: string | null;
  currency: string | null;
}

export interface SymbolSearchHit {
  symbol: string;
  name: string;
  asset_type: AssetType;
  exchange: string | null;
}

export interface SymbolSearchResponse {
  query: string;
  results: SymbolSearchHit[];
}

/** Name-based Yahoo Finance autocomplete. Users can type "apple" or "bitcoin"
 *  instead of needing to know the ticker. Selection drives the existing
 *  `lookupAsset` + `createAsset` flow. */
export function searchAssets(
  q: string,
  opts: { limit?: number; signal?: AbortSignal } = {},
): Promise<SymbolSearchResponse> {
  return apiGet<SymbolSearchResponse>("/api/assets/search/", {
    params: { q, limit: opts.limit ?? 10 },
    signal: opts.signal,
  });
}

export function lookupAsset(
  symbol: string,
  signal?: AbortSignal,
): Promise<AssetLookup> {
  return apiPost<AssetLookup, { symbol: string }>(
    "/api/assets/lookup/",
    { symbol },
    { signal },
  );
}

export interface CreateAssetResult {
  asset: Asset;
  bars_ingested: number;
  added_to_watchlist: boolean;
  /**
   * True iff the asset was freshly resolved+persisted by this call. False
   * means it already existed in the assets table and we skipped the yfinance
   * round-trip — the backend is idempotent on duplicate symbols, so the
   * end-state is still "asset tracked + linked to requested watchlist(s)".
   */
  newly_added: boolean;
}

export function createAsset(
  body: {
    symbol: string;
    add_to_default_watchlist?: boolean;
    /**
     * When set, the backend also links the (possibly pre-existing) asset to
     * this watchlist. Matches the "Track new…" button on a non-default list
     * — one POST handles both "resolve + persist" and "link to my list" so
     * an already-tracked asset isn't surfaced as a 409.
     */
    watchlist_id?: number | null;
  },
  signal?: AbortSignal,
): Promise<CreateAssetResult> {
  return apiPost<CreateAssetResult, typeof body>(
    "/api/assets/",
    body,
    { signal },
  );
}

// ---------- Prices ----------

export interface PricePoint {
  timestamp: string; // ISO 8601 — treat as UTC
  open: string; // Decimal-as-string
  high: string;
  low: string;
  close: string;
  volume: number;
}

export interface PriceSeries {
  symbol: string;
  count: number;
  points: PricePoint[];
}

export function getPriceSeries(
  symbol: string,
  opts: {
    from?: string;
    to?: string;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<PriceSeries> {
  return apiGet<PriceSeries>(`/api/prices/${encodeURIComponent(symbol)}/`, {
    params: {
      from: opts.from,
      to: opts.to,
      limit: opts.limit,
    },
    signal: opts.signal,
  });
}

// ---------- Macro ----------

export interface MacroIndicator {
  id: number;
  series_id: string;
  name: string;
  description: string | null;
  units: string | null;
  frequency: string | null;
  is_active: boolean;
}

export interface MacroDataPoint {
  date: string; // YYYY-MM-DD
  value: string; // Decimal-as-string
}

export interface MacroSeries {
  series_id: string;
  count: number;
  points: MacroDataPoint[];
}

export function listMacroIndicators(
  opts: { activeOnly?: boolean; signal?: AbortSignal } = {},
): Promise<MacroIndicator[]> {
  return apiGet<MacroIndicator[]>("/api/macro/", {
    params: { active_only: opts.activeOnly ?? true },
    signal: opts.signal,
  });
}

export function getMacroSeries(
  seriesId: string,
  opts: {
    from?: string;
    to?: string;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<MacroSeries> {
  return apiGet<MacroSeries>(`/api/macro/${encodeURIComponent(seriesId)}/`, {
    params: {
      from: opts.from,
      to: opts.to,
      limit: opts.limit,
    },
    signal: opts.signal,
  });
}

// ---------- News ----------

export type SentimentBucket = "positive" | "neutral" | "negative";

export interface Article {
  id: number;
  url: string;
  headline: string;
  source: string;
  published_at: string; // ISO 8601 — treat as UTC
  summary: string | null;
  /** VADER compound score in [-1, +1]; null = not yet scored. */
  sentiment: number | null;
  symbols: string[];
}

export interface ArticleList {
  count: number;
  articles: Article[];
}

export interface SentimentSummary {
  symbol: string;
  days: number;
  total: number;
  scored: number;
  unscored: number;
  positive: number;
  neutral: number;
  negative: number;
  /** Average compound score across scored articles in window; null if none. */
  mean: number | null;
}

export function listNews(
  opts: {
    symbol?: string;
    from?: string;
    to?: string;
    sentiment?: SentimentBucket;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<ArticleList> {
  return apiGet<ArticleList>("/api/news/", {
    params: {
      symbol: opts.symbol,
      from: opts.from,
      to: opts.to,
      sentiment: opts.sentiment,
      limit: opts.limit,
    },
    signal: opts.signal,
  });
}

export function getSentimentSummary(
  symbol: string,
  opts: { days?: number; signal?: AbortSignal } = {},
): Promise<SentimentSummary> {
  return apiGet<SentimentSummary>(
    `/api/news/sentiment-summary/${encodeURIComponent(symbol)}/`,
    {
      params: { days: opts.days ?? 7 },
      signal: opts.signal,
    },
  );
}

export interface SentimentTimeseriesPoint {
  date: string; // YYYY-MM-DD UTC
  mean: number;
  count: number;
}

export interface SentimentTimeseries {
  symbol: string;
  days: number;
  points: SentimentTimeseriesPoint[];
}

/** Daily-bucketed mean compound score for one asset; powers the
 *  "sentiment vs price" overlay on AssetDetail. */
export function getSentimentTimeseries(
  symbol: string,
  opts: { days?: number; signal?: AbortSignal } = {},
): Promise<SentimentTimeseries> {
  return apiGet<SentimentTimeseries>(
    `/api/news/sentiment-timeseries/${encodeURIComponent(symbol)}/`,
    {
      params: { days: opts.days ?? 30 },
      signal: opts.signal,
    },
  );
}

/** VADER-conventional thresholds — match `ml.sentiment` constants. */
export const SENTIMENT_POSITIVE_THRESHOLD = 0.05;
export const SENTIMENT_NEGATIVE_THRESHOLD = -0.05;

export function classifySentiment(score: number | null): SentimentBucket {
  if (score === null) return "neutral";
  if (score >= SENTIMENT_POSITIVE_THRESHOLD) return "positive";
  if (score <= SENTIMENT_NEGATIVE_THRESHOLD) return "negative";
  return "neutral";
}

// ---------- Config / runtime settings ----------

export type SettingType = "bool" | "int" | "string" | "secret";
export type SettingSource = "default" | "env" | "db";

export interface SettingEntry {
  key: string;
  type: SettingType;
  label: string;
  description: string;
  /** Current value. `null` for secrets (value is never returned verbatim). */
  value: number | boolean | string | null;
  source: SettingSource;
  /** Matching env var name (`FINTRACK_...`) or null if no env fallback. */
  env_name: string | null;
  min: number | null;
  max: number | null;
  /** For secrets: whether any value is set (env or db). Always true otherwise. */
  has_value: boolean;
  /** When set, the UI renders this as a select with these literal options
   *  instead of a free-form input. STRING-typed settings only. */
  allowed_values: string[] | null;
}

export interface ReadonlyConfig {
  db_path: string;
  port: number;
  log_level: string;
  enable_scheduler: boolean;
  enable_seed: boolean;
}

export interface AppConfig {
  settings: SettingEntry[];
  readonly: ReadonlyConfig;
}

export type ConfigUpdateValue = number | boolean | string;

export function getConfig(signal?: AbortSignal): Promise<AppConfig> {
  return apiGet<AppConfig>("/api/config/", { signal });
}

export function putConfig(
  updates: Record<string, ConfigUpdateValue>,
  signal?: AbortSignal,
): Promise<AppConfig> {
  return apiPut<AppConfig, { updates: Record<string, ConfigUpdateValue> }>(
    "/api/config/",
    { updates },
    { signal },
  );
}

// ---------- Watchlists ----------

export interface WatchlistSummary {
  id: number;
  name: string;
  is_default: boolean;
  item_count: number;
}

export interface WatchlistItem {
  asset_id: number;
  symbol: string;
  name: string;
  asset_type: string;
  position: number;
}

export interface WatchlistDetail {
  id: number;
  name: string;
  is_default: boolean;
  items: WatchlistItem[];
}

export interface WatchlistList {
  watchlists: WatchlistSummary[];
}

export function listWatchlists(signal?: AbortSignal): Promise<WatchlistList> {
  return apiGet<WatchlistList>("/api/watchlists/", { signal });
}

export function getDefaultWatchlist(
  signal?: AbortSignal,
): Promise<WatchlistDetail> {
  return apiGet<WatchlistDetail>("/api/watchlists/default/", { signal });
}

export function getWatchlist(
  id: number,
  signal?: AbortSignal,
): Promise<WatchlistDetail> {
  return apiGet<WatchlistDetail>(`/api/watchlists/${id}/`, { signal });
}

export function createWatchlist(
  body: { name: string; is_default?: boolean },
  signal?: AbortSignal,
): Promise<WatchlistSummary> {
  return apiPost<WatchlistSummary, { name: string; is_default?: boolean }>(
    "/api/watchlists/",
    body,
    { signal },
  );
}

export function updateWatchlist(
  id: number,
  body: { name?: string; is_default?: boolean },
  signal?: AbortSignal,
): Promise<WatchlistSummary> {
  return apiPut<WatchlistSummary, typeof body>(
    `/api/watchlists/${id}/`,
    body,
    { signal },
  );
}

export function deleteWatchlist(
  id: number,
  signal?: AbortSignal,
): Promise<void> {
  return apiDelete(`/api/watchlists/${id}/`, { signal });
}

export function addWatchlistItem(
  id: number,
  assetId: number,
  signal?: AbortSignal,
): Promise<WatchlistItem> {
  return apiPost<WatchlistItem, { asset_id: number }>(
    `/api/watchlists/${id}/items/`,
    { asset_id: assetId },
    { signal },
  );
}

export function removeWatchlistItem(
  id: number,
  assetId: number,
  signal?: AbortSignal,
): Promise<void> {
  return apiDelete(`/api/watchlists/${id}/items/${assetId}/`, { signal });
}

export function reorderWatchlistItems(
  id: number,
  assetIds: number[],
  signal?: AbortSignal,
): Promise<void> {
  return apiPut<void, { asset_ids: number[] }>(
    `/api/watchlists/${id}/items/reorder`,
    { asset_ids: assetIds },
    { signal },
  );
}

// ---------- Price Alerts ----------

export type AlertDirection = "above" | "below";
export type AlertMetric = "price" | "sentiment";

export interface PriceAlert {
  id: number;
  asset_id: number;
  symbol: string;
  asset_name: string;
  threshold: string; // Decimal-as-string
  direction: AlertDirection;
  /** Which signal the alert thresholds. "price" → latest close;
   *  "sentiment" → rolling-mean compound score over `window_days`. */
  metric: AlertMetric;
  /** Rolling-window length in days for sentiment alerts. Null otherwise. */
  window_days: number | null;
  is_active: boolean;
  triggered_at: string | null; // ISO 8601 or null
  notified_at: string | null;
  note: string | null;
  created_at: string;
  last_price: string | null; // Decimal-as-string, null if no bars yet
  last_price_at: string | null;
  /** The metric's most recent observed value (latest close for price
   *  alerts, rolling-mean sentiment for sentiment alerts). */
  current_value: string | null;
}

export interface AlertList {
  count: number;
  alerts: PriceAlert[];
}

export function listAlerts(
  opts: {
    assetId?: number;
    activeOnly?: boolean;
    signal?: AbortSignal;
  } = {},
): Promise<AlertList> {
  return apiGet<AlertList>("/api/alerts/", {
    params: {
      asset_id: opts.assetId,
      active_only: opts.activeOnly,
    },
    signal: opts.signal,
  });
}

export function listPendingAlertNotifications(
  signal?: AbortSignal,
): Promise<AlertList> {
  return apiGet<AlertList>("/api/alerts/pending-notifications/", { signal });
}

export function getAlert(
  id: number,
  signal?: AbortSignal,
): Promise<PriceAlert> {
  return apiGet<PriceAlert>(`/api/alerts/${id}/`, { signal });
}

export interface CreateAlertBody {
  asset_id: number;
  threshold: string | number;
  direction: AlertDirection;
  note?: string | null;
  /** Default "price". Set to "sentiment" + provide `window_days` to
   *  fire on rolling-mean sentiment crossings instead of price ones. */
  metric?: AlertMetric;
  window_days?: number | null;
}

export function createAlert(
  body: CreateAlertBody,
  signal?: AbortSignal,
): Promise<PriceAlert> {
  return apiPost<PriceAlert, CreateAlertBody>("/api/alerts/", body, { signal });
}

export interface UpdateAlertBody {
  threshold?: string | number;
  direction?: AlertDirection;
  is_active?: boolean;
  note?: string | null;
  reset?: boolean;
}

export function updateAlert(
  id: number,
  body: UpdateAlertBody,
  signal?: AbortSignal,
): Promise<PriceAlert> {
  return apiPut<PriceAlert, UpdateAlertBody>(`/api/alerts/${id}/`, body, {
    signal,
  });
}

export function deleteAlert(id: number, signal?: AbortSignal): Promise<void> {
  return apiDelete(`/api/alerts/${id}/`, { signal });
}

export function markAlertNotified(
  id: number,
  signal?: AbortSignal,
): Promise<PriceAlert> {
  return apiPost<PriceAlert, Record<string, never>>(
    `/api/alerts/${id}/mark-notified/`,
    {},
    { signal },
  );
}

// ---------- Portfolio ----------

export type TransactionType = "buy" | "sell";

export interface PortfolioTransaction {
  id: number;
  asset_id: number;
  symbol: string;
  asset_name: string;
  transaction_type: TransactionType;
  quantity: string; // Decimal-as-string
  price_per_unit: string;
  transaction_date: string; // YYYY-MM-DD
  fee: string;
  notes: string | null;
  created_at: string;
}

export interface PortfolioPosition {
  asset_id: number;
  symbol: string;
  asset_name: string;
  quantity: string;
  avg_cost: string;
  cost_basis: string;
  realized_pl: string;
  last_close: string | null;
  last_close_at: string | null;
  current_value: string | null;
  unrealized_pl: string | null;
  unrealized_pl_pct: string | null;
  transaction_count: number;
}

export interface PortfolioSummary {
  total_cost_basis: string;
  total_current_value: string;
  total_unrealized_pl: string;
  total_unrealized_pl_pct: string | null;
  total_realized_pl: string;
  open_positions: number;
}

export interface CreateTransactionBody {
  asset_id: number;
  transaction_type: TransactionType;
  quantity: string | number;
  price_per_unit: string | number;
  transaction_date: string; // YYYY-MM-DD
  fee?: string | number;
  notes?: string | null;
}

export function listPortfolioTransactions(
  opts: { assetId?: number; signal?: AbortSignal } = {},
): Promise<{ count: number; transactions: PortfolioTransaction[] }> {
  return apiGet("/api/portfolio/transactions/", {
    params: { asset_id: opts.assetId },
    signal: opts.signal,
  });
}

export function createPortfolioTransaction(
  body: CreateTransactionBody,
  signal?: AbortSignal,
): Promise<PortfolioTransaction> {
  return apiPost<PortfolioTransaction, CreateTransactionBody>(
    "/api/portfolio/transactions/",
    body,
    { signal },
  );
}

export function deletePortfolioTransaction(
  id: number,
  signal?: AbortSignal,
): Promise<void> {
  return apiDelete(`/api/portfolio/transactions/${id}/`, { signal });
}

export function listPortfolioPositions(
  signal?: AbortSignal,
): Promise<{ count: number; positions: PortfolioPosition[] }> {
  return apiGet("/api/portfolio/positions/", { signal });
}

export function getPortfolioSummary(
  signal?: AbortSignal,
): Promise<PortfolioSummary> {
  return apiGet("/api/portfolio/summary/", { signal });
}

export interface PerformancePoint {
  date: string; // YYYY-MM-DD
  value: string;
  cost_basis: string;
  realized_pl: string;
}

export interface PortfolioPerformance {
  lookback_days: number;
  points: PerformancePoint[];
}

export function getPortfolioPerformance(
  opts: { lookbackDays?: number; signal?: AbortSignal } = {},
): Promise<PortfolioPerformance> {
  return apiGet<PortfolioPerformance>("/api/portfolio/performance/", {
    params: { lookback_days: opts.lookbackDays ?? 90 },
    signal: opts.signal,
  });
}

// ---------- Forecast ----------

/** Server-supported engines. Stays as a literal union here so the UI can
 *  present a typed selector without doing its own validation. */
export type ForecastEngine = "sarimax" | "holt_winters";

export interface ForecastPoint {
  forecast_date: string; // YYYY-MM-DD
  yhat: number;
  lower_80: number;
  upper_80: number;
  lower_95: number;
  upper_95: number;
}

export interface Forecast {
  symbol: string;
  asset_id: number;
  model: string;
  horizon_days: number;
  training_rows: number;
  last_close: string; // Decimal → serialised as string by Pydantic
  last_close_date: string; // YYYY-MM-DD
  generated_at: string; // ISO 8601 (may lack tz — treat as UTC)
  points: ForecastPoint[];
}

export interface ForecastAvailability {
  eligible: string[];
  persisted: string[];
  /** Canonical list of engines the backend will accept (empty until the
   *  endpoint is reached at least once on a fresh install). */
  engines: string[];
}

export interface RetrainAllResult {
  requested: number;
  trained: number;
  skipped: number;
  engine: string;
}

export interface ClearForecastsResult {
  deleted: number;
}

export interface ScoreNowResult {
  scored: number;
}

export function listForecastAvailability(
  signal?: AbortSignal,
): Promise<ForecastAvailability> {
  return apiGet<ForecastAvailability>("/api/forecast/", { signal });
}

export function getForecast(
  symbol: string,
  signal?: AbortSignal,
): Promise<Forecast> {
  return apiGet<Forecast>(`/api/forecast/${encodeURIComponent(symbol)}/`, {
    signal,
  });
}

export function retrainForecast(
  symbol: string,
  opts: { engine?: ForecastEngine | null; signal?: AbortSignal } = {},
): Promise<Forecast> {
  const qs = opts.engine ? `?engine=${encodeURIComponent(opts.engine)}` : "";
  return apiPost<Forecast, Record<string, never>>(
    `/api/forecast/${encodeURIComponent(symbol)}/retrain/${qs}`,
    {},
    { signal: opts.signal },
  );
}

export interface EngineAccuracyEntry {
  engine: string;
  snapshots: number;
  evaluable_points: number;
  /** Mean absolute percentage error (null when no evaluable pairs yet). */
  mape: number | null;
  /** Root mean squared error in price units. */
  rmse: number | null;
  /** Fraction of forecasts that called direction correctly (0..1). */
  directional: number | null;
}

export interface ForecastAccuracyReport {
  symbol: string;
  days: number;
  per_engine: EngineAccuracyEntry[];
  overall: EngineAccuracyEntry | null;
  actuals_available: number;
}

/** Rolling forecast accuracy for one asset. Drives the "How accurate has
 *  the forecaster been?" panel on AssetDetail and is the headline answer
 *  to "should I switch engines?". */
export function getForecastAccuracy(
  symbol: string,
  opts: { days?: number; signal?: AbortSignal } = {},
): Promise<ForecastAccuracyReport> {
  return apiGet<ForecastAccuracyReport>(
    `/api/forecast/${encodeURIComponent(symbol)}/accuracy/`,
    {
      params: { days: opts.days ?? 30 },
      signal: opts.signal,
    },
  );
}

export interface VolatilityReport {
  symbol: string;
  lookback_days: number;
  returns_used: int;
  last_close: number | null;
  last_close_date: string | null; // YYYY-MM-DD
  /** Daily realized vol — decimal proportion (0.012 == 1.2%). */
  realized_vol_daily: number | null;
  /** Annualised realized vol (daily × sqrt(252)). */
  realized_vol_annualized: number | null;
  /** EWMA next-day forecast vol — decimal proportion. */
  ewma_next_day_vol: number | null;
  /** ±1σ price-space band for the next trading day. */
  expected_move_low: number | null;
  expected_move_high: number | null;
}

// TS doesn't have a built-in `int`; alias to number so the field reads
// closer to the wire schema.
type int = number;

/** Realized + EWMA-forecast volatility for a single asset. Drives the
 *  "Risk profile" panel on AssetDetail. Returns metrics as decimal
 *  proportions; UI formats as percentages. */
export function getVolatility(
  symbol: string,
  opts: { lookbackDays?: number; signal?: AbortSignal } = {},
): Promise<VolatilityReport> {
  return apiGet<VolatilityReport>(
    `/api/forecast/${encodeURIComponent(symbol)}/volatility/`,
    {
      params: { lookback_days: opts.lookbackDays ?? 30 },
      signal: opts.signal,
    },
  );
}

// ---------- Analytics ----------

export interface CorrelationCell {
  symbol_a: string;
  symbol_b: string;
  /** Pearson r in [-1, +1]. */
  coefficient: number;
  /** Number of overlapping return-days backing the correlation. */
  overlap: number;
}

export interface CorrelationMatrix {
  symbols: string[];
  lookback_days: number;
  asset_count: number;
  /** Server's own MIN_OVERLAP_DAYS threshold — UI fades cells below this. */
  min_overlap_days: number;
  /** Upper triangle + diagonal. UI mirrors when rendering the lower half. */
  cells: CorrelationCell[];
}

export function getCorrelations(
  opts: {
    symbols: string[];
    lookbackDays?: number;
    signal?: AbortSignal;
  },
): Promise<CorrelationMatrix> {
  return apiGet<CorrelationMatrix>("/api/analytics/correlations/", {
    params: {
      symbols: opts.symbols.join(","),
      lookback_days: opts.lookbackDays ?? 90,
    },
    signal: opts.signal,
  });
}

export function getDefaultWatchlistCorrelations(
  opts: { lookbackDays?: number; signal?: AbortSignal } = {},
): Promise<CorrelationMatrix> {
  return apiGet<CorrelationMatrix>(
    "/api/analytics/correlations/default-watchlist/",
    {
      params: { lookback_days: opts.lookbackDays ?? 90 },
      signal: opts.signal,
    },
  );
}

export function retrainAllForecasts(
  opts: { engine?: ForecastEngine | null; signal?: AbortSignal } = {},
): Promise<RetrainAllResult> {
  const qs = opts.engine ? `?engine=${encodeURIComponent(opts.engine)}` : "";
  return apiPost<RetrainAllResult, Record<string, never>>(
    `/api/forecast/retrain-all/${qs}`,
    {},
    { signal: opts.signal },
  );
}

export function clearAllForecasts(
  signal?: AbortSignal,
): Promise<ClearForecastsResult> {
  // The endpoint returns a JSON body (`{deleted: N}`) so we route through
  // the JSON helper rather than the void-returning `apiDelete`.
  return apiJson<ClearForecastsResult, Record<string, never>>(
    "DELETE",
    "/api/forecast/",
    {},
    { signal },
  );
}

export function scoreArticlesNow(
  signal?: AbortSignal,
): Promise<ScoreNowResult> {
  return apiPost<ScoreNowResult, Record<string, never>>(
    "/api/news/score-now/",
    {},
    { signal },
  );
}
