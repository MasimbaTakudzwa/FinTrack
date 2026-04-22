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
  method: "POST" | "PUT",
  path: string,
  body: B,
  opts: { signal?: AbortSignal },
): Promise<T> {
  const base = await getBaseUrl();
  const url = `${base}${path}`;
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: opts.signal,
  });
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
}

export function createAsset(
  body: { symbol: string; add_to_default_watchlist?: boolean },
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

export interface Article {
  id: number;
  url: string;
  headline: string;
  source: string;
  published_at: string; // ISO 8601 — treat as UTC
  summary: string | null;
  symbols: string[];
}

export interface ArticleList {
  count: number;
  articles: Article[];
}

export function listNews(
  opts: {
    symbol?: string;
    from?: string;
    to?: string;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<ArticleList> {
  return apiGet<ArticleList>("/api/news/", {
    params: {
      symbol: opts.symbol,
      from: opts.from,
      to: opts.to,
      limit: opts.limit,
    },
    signal: opts.signal,
  });
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

export interface PriceAlert {
  id: number;
  asset_id: number;
  symbol: string;
  asset_name: string;
  threshold: string; // Decimal-as-string
  direction: AlertDirection;
  is_active: boolean;
  triggered_at: string | null; // ISO 8601 or null
  notified_at: string | null;
  note: string | null;
  created_at: string;
  last_price: string | null; // Decimal-as-string, null if no bars yet
  last_price_at: string | null;
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
