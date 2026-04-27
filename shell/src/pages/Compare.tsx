import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { GitCompare, Loader2, RefreshCw, X } from "lucide-react";
import {
  type Asset,
  type PriceSeries,
  getPriceSeries,
  listAssets,
} from "../api/client";
import { ComparisonChart } from "../components/ComparisonChart";
import { type UTCTimestamp } from "lightweight-charts";

interface State {
  assets: Asset[];
  series: Record<string, PriceSeries>;
  loading: boolean;
  error: string | null;
}

const INITIAL: State = {
  assets: [],
  series: {},
  loading: true,
  error: null,
};

interface LookbackOption {
  value: number;
  label: string;
}

const LOOKBACK_OPTIONS: LookbackOption[] = [
  { value: 30, label: "30d" },
  { value: 90, label: "90d" },
  { value: 180, label: "180d" },
  { value: 365, label: "1y" },
  { value: 0, label: "All" },
];

/** Distinct, colorblind-friendly series colors. Cycles if the user adds
 *  more than ``LINE_COLORS.length`` symbols. */
const LINE_COLORS = [
  "#10b981", // emerald-500
  "#6366f1", // indigo-500
  "#f59e0b", // amber-500
  "#ef4444", // red-500
  "#06b6d4", // cyan-500
  "#a855f7", // purple-500
  "#84cc16", // lime-500
  "#ec4899", // pink-500
];

const DEFAULT_SYMBOLS = ["AAPL", "MSFT", "SPY"];
const MAX_SYMBOLS = 8;

/**
 * Multi-asset comparison page. Pick 2–8 assets, see their prices
 * normalized to 100 at the start of the window and overlaid on one
 * chart. Answers "which of these has had the better run?" without
 * eyeballing absolute prices that span orders of magnitude.
 *
 * URL state holds both the symbol list and the lookback window so
 * users can deep-link comparisons.
 */
export function Compare() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [state, setState] = useState<State>(INITIAL);
  const [tick, setTick] = useState(0);

  const urlSymbols = useMemo(() => {
    const raw = searchParams.get("symbols");
    if (!raw) return DEFAULT_SYMBOLS;
    return raw
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
  }, [searchParams]);

  const lookback = useMemo(() => {
    const raw = searchParams.get("days");
    const n = raw ? Number(raw) : 90;
    if (!Number.isFinite(n) || n < 0) return 90;
    return n;
  }, [searchParams]);

  // Fetch the asset catalog once + the price series for each tracked symbol.
  // Series fetched in parallel; per-symbol failures are surfaced but don't
  // block other symbols from rendering.
  useEffect(() => {
    const ac = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const assets = await listAssets({
          activeOnly: false,
          signal: ac.signal,
        });
        const knownSymbols = new Set(assets.map((a) => a.symbol));
        const wanted = urlSymbols.filter((s) => knownSymbols.has(s));
        const seriesEntries = await Promise.all(
          wanted.map((sym) =>
            getPriceSeries(sym, { limit: 5000, signal: ac.signal }).then(
              (ps) => [sym, ps] as const,
              () => [sym, null] as const,
            ),
          ),
        );
        if (cancelled) return;
        const series: Record<string, PriceSeries> = {};
        for (const [sym, ps] of seriesEntries) {
          if (ps) series[sym] = ps;
        }
        setState({ assets, series, loading: false, error: null });
      } catch (err) {
        if (cancelled || ac.signal.aborted) return;
        setState({
          assets: [],
          series: {},
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [urlSymbols, tick]);

  const onAddSymbol = (symbol: string) => {
    if (urlSymbols.includes(symbol)) return;
    if (urlSymbols.length >= MAX_SYMBOLS) return;
    const next = new URLSearchParams(searchParams);
    next.set("symbols", [...urlSymbols, symbol].join(","));
    setSearchParams(next);
    setState((s) => ({ ...s, loading: true }));
  };

  const onRemoveSymbol = (symbol: string) => {
    if (urlSymbols.length <= 1) return; // keep at least one
    const next = new URLSearchParams(searchParams);
    const remaining = urlSymbols.filter((s) => s !== symbol);
    next.set("symbols", remaining.join(","));
    setSearchParams(next);
    setState((s) => ({ ...s, loading: true }));
  };

  const onLookbackChange = (value: number) => {
    const next = new URLSearchParams(searchParams);
    if (value === 90) next.delete("days");
    else next.set("days", String(value));
    setSearchParams(next);
  };

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    setTick((t) => t + 1);
  };

  const comparison = useMemo(
    () =>
      buildComparison(state.series, urlSymbols, lookback),
    [state.series, urlSymbols, lookback],
  );

  const sortedAssets = useMemo(
    () =>
      [...state.assets]
        .filter((a) => !urlSymbols.includes(a.symbol))
        .sort((a, b) => a.symbol.localeCompare(b.symbol)),
    [state.assets, urlSymbols],
  );

  return (
    <div className="p-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            <GitCompare className="h-5 w-5 text-zinc-400" />
            Compare assets
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Each line is normalized to 100 at the start of the window —
            steeper slope = bigger relative move.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <LookbackPicker value={lookback} onChange={onLookbackChange} />
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
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {urlSymbols.map((sym, idx) => {
          const color = LINE_COLORS[idx % LINE_COLORS.length];
          const known = state.assets.some((a) => a.symbol === sym);
          return (
            <SymbolChip
              key={sym}
              symbol={sym}
              color={color}
              unknown={!known && state.assets.length > 0}
              onRemove={
                urlSymbols.length > 1 ? () => onRemoveSymbol(sym) : undefined
              }
            />
          );
        })}
        {urlSymbols.length < MAX_SYMBOLS && sortedAssets.length > 0 && (
          <select
            onChange={(e) => {
              const v = e.target.value;
              if (v) onAddSymbol(v);
              e.target.value = "";
            }}
            value=""
            className="rounded-md border border-dashed border-zinc-300 bg-white px-2 py-1 text-xs font-medium text-zinc-600 hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
          >
            <option value="">+ add asset</option>
            {sortedAssets.map((a) => (
              <option key={a.id} value={a.symbol}>
                {a.symbol} — {a.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {state.error && (
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          {state.error}
        </div>
      )}

      {state.loading && Object.keys(state.series).length === 0 ? (
        <div className="flex items-center gap-2 py-10 text-sm text-zinc-500 dark:text-zinc-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading price series for {urlSymbols.length} asset
          {urlSymbols.length === 1 ? "" : "s"}…
        </div>
      ) : comparison.series.length === 0 ? (
        <div className="rounded-md border border-zinc-200 bg-white p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
          None of the selected symbols have enough price data to chart
          yet. Try a longer lookback or different assets.
        </div>
      ) : (
        <>
          <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
            <ComparisonChart series={comparison.series} />
          </div>

          <PerformanceTable series={comparison.series} />
        </>
      )}
    </div>
  );
}

interface BuiltSeries {
  symbol: string;
  color: string;
  points: { time: UTCTimestamp; value: number }[];
  startPrice: number;
  endPrice: number;
  changePct: number;
}

function buildComparison(
  series: Record<string, PriceSeries>,
  symbols: string[],
  lookbackDays: number,
): { series: BuiltSeries[] } {
  const now = Date.now();
  const cutoffMs =
    lookbackDays === 0 ? 0 : now - lookbackDays * 24 * 60 * 60 * 1000;
  const out: BuiltSeries[] = [];

  for (let i = 0; i < symbols.length; i++) {
    const sym = symbols[i];
    const ps = series[sym];
    if (!ps || ps.points.length === 0) continue;
    // Filter to the lookback window.
    const inWindow = ps.points.filter((p) => parseTs(p.timestamp) >= cutoffMs);
    if (inWindow.length === 0) continue;
    const first = Number(inWindow[0].close);
    if (!first || !Number.isFinite(first)) continue;
    const points = inWindow.map((p) => ({
      time: (parseTs(p.timestamp) / 1000) as UTCTimestamp,
      value: (Number(p.close) / first) * 100,
    }));
    const last = Number(inWindow[inWindow.length - 1].close);
    out.push({
      symbol: sym,
      color: LINE_COLORS[i % LINE_COLORS.length],
      points,
      startPrice: first,
      endPrice: last,
      changePct: ((last - first) / first) * 100,
    });
  }
  return { series: out };
}

function parseTs(iso: string): number {
  // Sidecar emits naive UTC; coerce by appending Z when missing.
  const s = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return Date.parse(s);
}

function SymbolChip({
  symbol,
  color,
  unknown,
  onRemove,
}: {
  symbol: string;
  color: string;
  unknown?: boolean;
  onRemove?: () => void;
}) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium",
        unknown
          ? "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
          : "border-zinc-200 bg-white text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200",
      ].join(" ")}
    >
      <span
        className="h-2 w-2 rounded-full"
        style={{ background: color }}
        aria-hidden
      />
      <span className="font-mono">{symbol}</span>
      {unknown && <span className="text-[10px]">(not tracked)</span>}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${symbol}`}
          className="rounded p-0.5 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}

function LookbackPicker({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-zinc-200 bg-white p-0.5 dark:border-zinc-700 dark:bg-zinc-900">
      {LOOKBACK_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`rounded px-2.5 py-1 text-xs font-medium ${
            value === opt.value
              ? "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
              : "text-zinc-500 hover:bg-zinc-50 dark:text-zinc-400 dark:hover:bg-zinc-800"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function PerformanceTable({ series }: { series: BuiltSeries[] }) {
  const ranked = [...series].sort((a, b) => b.changePct - a.changePct);
  return (
    <div className="mt-4 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
      <table className="w-full text-left text-sm">
        <thead className="bg-zinc-50 text-[10px] uppercase tracking-wide text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
          <tr>
            <th className="px-4 py-2 font-medium">Symbol</th>
            <th className="px-4 py-2 text-right font-medium">Start</th>
            <th className="px-4 py-2 text-right font-medium">End</th>
            <th className="px-4 py-2 text-right font-medium">Change</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {ranked.map((row) => {
            const tone =
              row.changePct > 0
                ? "text-emerald-600 dark:text-emerald-400"
                : row.changePct < 0
                  ? "text-rose-600 dark:text-rose-400"
                  : "text-zinc-500";
            return (
              <tr key={row.symbol}>
                <td className="px-4 py-2">
                  <span className="inline-flex items-center gap-2">
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ background: row.color }}
                      aria-hidden
                    />
                    <span className="font-mono font-semibold">{row.symbol}</span>
                  </span>
                </td>
                <td className="px-4 py-2 text-right font-mono tabular-nums text-zinc-700 dark:text-zinc-300">
                  {fmtPrice(row.startPrice)}
                </td>
                <td className="px-4 py-2 text-right font-mono tabular-nums text-zinc-700 dark:text-zinc-300">
                  {fmtPrice(row.endPrice)}
                </td>
                <td className={`px-4 py-2 text-right font-mono tabular-nums ${tone}`}>
                  {row.changePct > 0 ? "+" : ""}
                  {row.changePct.toFixed(2)}%
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function fmtPrice(n: number): string {
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(2);
  return n.toFixed(4);
}
