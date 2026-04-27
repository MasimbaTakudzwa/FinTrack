import { Link } from "react-router-dom";
import { PieChart } from "lucide-react";
import type { PortfolioPosition } from "../api/client";

interface Props {
  positions: PortfolioPosition[];
}

/**
 * Visual breakdown of how the portfolio's current value is divided
 * across open positions. Renders a horizontal-bar list rather than a
 * pie chart — bars are faster to scan, support exact-percentage
 * labels, and don't need a chart library. Closed positions are
 * filtered out (qty=0). Sorted by value descending.
 *
 * When all positions lack a `current_value` (no daily-close backfill
 * yet), the card collapses to a one-line empty state rather than
 * rendering a meaningless full-width "no data" bar.
 */
export function PortfolioAllocationCard({ positions }: Props) {
  const open = positions.filter((p) => Number(p.quantity) > 0);
  if (open.length === 0) {
    return null; // no open positions → nothing to allocate
  }

  const allocations = open
    .map((p) => ({
      symbol: p.symbol,
      assetName: p.asset_name,
      assetId: p.asset_id,
      value: p.current_value !== null ? Number(p.current_value) : 0,
      hasCurrentValue: p.current_value !== null,
    }))
    .sort((a, b) => b.value - a.value);

  const total = allocations.reduce((sum, a) => sum + a.value, 0);

  if (total === 0) {
    return (
      <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
        <Header />
        <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
          No daily-close data yet for any open position — allocation
          requires at least one current price per asset.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <Header />
      <ul className="mt-3 space-y-2">
        {allocations.map((a, idx) => {
          const pct = (a.value / total) * 100;
          const palette = ALLOCATION_COLORS[idx % ALLOCATION_COLORS.length];
          return (
            <li key={a.assetId}>
              <div className="flex items-baseline justify-between gap-2 text-sm">
                <Link
                  to={`/assets/${encodeURIComponent(a.symbol)}`}
                  className="inline-flex items-center gap-2 font-mono font-semibold text-zinc-800 hover:text-emerald-700 dark:text-zinc-200 dark:hover:text-emerald-400"
                >
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ background: palette }}
                    aria-hidden
                  />
                  {a.symbol}
                </Link>
                <span className="text-xs text-zinc-500 dark:text-zinc-400">
                  {a.hasCurrentValue ? (
                    <>
                      {fmtUSD(a.value)} ·{" "}
                      <span className="tabular-nums">{pct.toFixed(1)}%</span>
                    </>
                  ) : (
                    "no current price"
                  )}
                </span>
              </div>
              <div
                className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"
                role="img"
                aria-label={`${a.symbol}: ${pct.toFixed(1)}% of portfolio`}
              >
                <div
                  className="h-full rounded-full transition-[width] duration-200"
                  style={{ width: `${pct}%`, background: palette }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Header() {
  return (
    <header className="flex items-center justify-between">
      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        <PieChart className="h-3.5 w-3.5 text-indigo-500" />
        Allocation
      </h3>
    </header>
  );
}

function fmtUSD(n: number): string {
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

const ALLOCATION_COLORS = [
  "#10b981",
  "#6366f1",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#a855f7",
  "#84cc16",
  "#ec4899",
  "#f97316",
  "#14b8a6",
];
