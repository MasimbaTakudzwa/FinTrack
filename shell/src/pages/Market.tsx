import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDownRight, ArrowUpRight, RefreshCw } from "lucide-react";
import {
  type Asset,
  type AssetType,
  type PriceSeries,
  getPriceSeries,
  listAssets,
} from "../api/client";

interface MoverRow {
  asset: Asset;
  lastClose: number | null;
  changePct: number | null;
}

interface State {
  rows: MoverRow[];
  loading: boolean;
  error: string | null;
}

const INITIAL: State = { rows: [], loading: true, error: null };

// 5-min bars × 288 ≈ 24 hours of intraday data. Enough to make the rolling 24h
// change meaningful even when the latest two bars are flat (off-hours etc.).
const CHANGE_WINDOW_BARS = 300;
const ONE_DAY_MS = 24 * 60 * 60 * 1000;

function fmtPrice(n: number): string {
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(2);
  return n.toFixed(4);
}

function fmtPct(pct: number): string {
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

/**
 * Compute "recent change %" for the movers board.
 *
 * Previously this was (last close − previous close) / previous close, but with
 * 5-min bars that window is so short that two adjacent closes are often
 * identical (weekends, pre/post-market) and every asset ends up excluded from
 * both gainers and losers.
 *
 * Now: find the oldest bar whose timestamp is within the last 24h; compute
 * change relative to that close. Falls back to the oldest bar in the window
 * when the data doesn't span 24h yet (fresh install).
 */
function toRow(asset: Asset, series: PriceSeries): MoverRow {
  const pts = series.points;
  if (pts.length === 0) return { asset, lastClose: null, changePct: null };
  const lastClose = Number(pts[pts.length - 1].close);
  if (pts.length < 2) return { asset, lastClose, changePct: null };

  const nowMs = Date.now();
  const cutoffMs = nowMs - ONE_DAY_MS;
  let anchorIdx = 0;
  for (let i = 0; i < pts.length - 1; i++) {
    const tsIso = pts[i].timestamp;
    // SQLite returns naive datetimes — coerce to UTC for a consistent parse.
    const normalised = /[zZ]|[+-]\d{2}:?\d{2}$/.test(tsIso) ? tsIso : `${tsIso}Z`;
    const t = Date.parse(normalised);
    if (!Number.isNaN(t) && t >= cutoffMs) {
      anchorIdx = i;
      break;
    }
  }
  const anchor = Number(pts[anchorIdx].close);
  if (!anchor) return { asset, lastClose, changePct: null };
  const changePct = ((lastClose - anchor) / anchor) * 100;
  return { asset, lastClose, changePct };
}

async function loadRows(signal: AbortSignal): Promise<MoverRow[]> {
  const assets = await listAssets({ signal });
  const rows: MoverRow[] = [];
  await Promise.all(
    assets.map(async (a) => {
      try {
        const series = await getPriceSeries(a.symbol, {
          limit: CHANGE_WINDOW_BARS,
          signal,
        });
        rows.push(toRow(a, series));
      } catch {
        rows.push({ asset: a, lastClose: null, changePct: null });
      }
    }),
  );
  return rows;
}

export function Market() {
  const [state, setState] = useState<State>(INITIAL);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const rows = await loadRows(controller.signal);
        if (!cancelled) setState({ rows, loading: false, error: null });
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        setState({
          rows: [],
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [tick]);

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    setTick((t) => t + 1);
  };

  const { gainers, losers, byType } = useMemo(() => {
    const ranked = state.rows.filter((r) => r.changePct !== null);
    ranked.sort((a, b) => (b.changePct ?? 0) - (a.changePct ?? 0));
    const gainers = ranked.filter((r) => (r.changePct ?? 0) > 0).slice(0, 5);
    const losers = ranked
      .filter((r) => (r.changePct ?? 0) < 0)
      .slice(-5)
      .reverse();

    const byType: Record<AssetType, number> = {
      stock: 0,
      etf: 0,
      crypto: 0,
      commodity: 0,
      index: 0,
    };
    for (const r of state.rows) byType[r.asset.asset_type] += 1;
    return { gainers, losers, byType };
  }, [state.rows]);

  return (
    <div className="p-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            Market overview
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {state.loading
              ? "Loading…"
              : `${state.rows.length} tracked assets · change vs. 24h earlier`}
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={state.loading}
          className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${state.loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {state.error && (
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          {state.error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <MoverPanel title="Top gainers" tone="up" rows={gainers} />
        <MoverPanel title="Top losers" tone="down" rows={losers} />
      </div>

      <div className="mt-4 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          By asset type
        </h3>
        <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
          {(Object.keys(byType) as AssetType[]).map((t) => (
            <div key={t} className="rounded-md bg-zinc-50 p-3 dark:bg-zinc-900">
              <dt className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                {t}
              </dt>
              <dd className="mt-0.5 text-xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                {byType[t]}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

function MoverPanel({
  title,
  tone,
  rows,
}: {
  title: string;
  tone: "up" | "down";
  rows: MoverRow[];
}) {
  const arrowCls = tone === "up"
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-rose-600 dark:text-rose-400";
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {title}
      </h3>
      {rows.length === 0 ? (
        <p className="text-sm text-zinc-400 dark:text-zinc-500">No data yet.</p>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {rows.map(({ asset, lastClose, changePct }) => (
            <li key={asset.id}>
              <Link
                to={`/assets/${encodeURIComponent(asset.symbol)}`}
                className="flex items-center justify-between py-2 hover:bg-zinc-50 dark:hover:bg-zinc-900"
              >
                <div>
                  <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    {asset.symbol}
                  </div>
                  <div className="text-xs text-zinc-500 dark:text-zinc-400">
                    {asset.name}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm tabular-nums text-zinc-700 dark:text-zinc-300">
                    {lastClose === null ? "—" : fmtPrice(lastClose)}
                  </span>
                  <span className={`inline-flex items-center gap-0.5 text-sm font-semibold ${arrowCls}`}>
                    {tone === "up" ? (
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    ) : (
                      <ArrowDownRight className="h-3.5 w-3.5" />
                    )}
                    {changePct === null ? "—" : fmtPct(changePct)}
                  </span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
