import { Link } from "react-router-dom";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import type { Asset, PriceSeries, Quote } from "../api/client";
import { Sparkline } from "./Sparkline";

interface Props {
  asset: Asset;
  series: PriceSeries | null;
  /** Server-computed day-change quote (canonical). Null falls back to last close. */
  quote?: Quote | null;
  loading?: boolean;
  error?: string | null;
}

function fmtPrice(value: number): string {
  if (value >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (value >= 1) return value.toFixed(2);
  return value.toFixed(4);
}

function fmtPct(pct: number): string {
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

export function AssetCard({ asset, series, quote, loading, error }: Props) {
  const closes = series?.points.map((p) => Number(p.close)) ?? [];
  // Last price prefers the server quote (which also drives the day change);
  // fall back to the latest sparkline close when the quote is unavailable.
  const quoteLast = quote?.last_price != null ? Number(quote.last_price) : null;
  const last = quoteLast ?? closes.at(-1) ?? null;
  // Day change is the server-computed previous-session figure, NOT a delta
  // between the last two 5-minute bars.
  const changePct = quote?.change_pct ?? null;

  const direction: "up" | "down" | "flat" =
    changePct === null ? "flat" : changePct > 0 ? "up" : changePct < 0 ? "down" : "flat";

  const toneClasses = {
    up: "text-emerald-600 dark:text-emerald-400",
    down: "text-rose-600 dark:text-rose-400",
    flat: "text-zinc-500 dark:text-zinc-400",
  }[direction];

  return (
    <Link
      to={`/assets/${encodeURIComponent(asset.symbol)}`}
      className="group flex flex-col gap-3 rounded-lg border border-zinc-200 bg-white p-4 transition-colors hover:border-zinc-300 hover:shadow-sm dark:border-zinc-800 dark:bg-zinc-950 dark:hover:border-zinc-700"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
              {asset.symbol}
            </span>
            <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
              {asset.asset_type}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-zinc-500 dark:text-zinc-400">
            {asset.name}
          </p>
        </div>
        <div className={`flex items-center gap-0.5 text-xs font-semibold ${toneClasses}`}>
          {direction === "up" && <ArrowUpRight className="h-3.5 w-3.5" />}
          {direction === "down" && <ArrowDownRight className="h-3.5 w-3.5" />}
          {direction === "flat" && <Minus className="h-3.5 w-3.5" />}
          <span>{changePct === null ? "—" : fmtPct(changePct)}</span>
        </div>
      </div>

      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-xl font-semibold tracking-tight text-zinc-900 tabular-nums dark:text-zinc-100">
            {last === null ? "—" : fmtPrice(last)}
          </div>
          <div className="mt-0.5 text-[10px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
            {loading && !series ? "Loading…" : error ? "Error" : series ? `${series.count} bars` : "No data"}
          </div>
        </div>
        <Sparkline values={closes} width={110} height={36} />
      </div>
    </Link>
  );
}
