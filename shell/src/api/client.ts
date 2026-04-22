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
