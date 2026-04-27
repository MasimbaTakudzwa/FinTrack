import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDownRight, ArrowUpRight, Briefcase, Minus } from "lucide-react";
import {
  getPortfolioSummary,
  type PortfolioSummary,
} from "../api/client";

type State =
  | { status: "idle"; data: null }
  | { status: "ready"; data: PortfolioSummary }
  | { status: "empty"; data: null };

const INITIAL: State = { status: "idle", data: null };

/**
 * Compact portfolio rollup for the Dashboard. Hides itself entirely
 * when there are no transactions yet (no `open_positions` and zero
 * realized P&L), so brand-new users aren't pushed to engage with
 * portfolio tracking before they've added any assets.
 *
 * Numbers come straight from /api/portfolio/summary/ — same source as
 * the Portfolio page header. Refresh is implicit per Dashboard mount;
 * the rollup is cheap enough that we don't bother with a tick prop.
 */
export function PortfolioSummaryStrip() {
  const [state, setState] = useState<State>(INITIAL);

  useEffect(() => {
    const ac = new AbortController();
    getPortfolioSummary(ac.signal)
      .then((data) => {
        if (ac.signal.aborted) return;
        if (
          data.open_positions === 0 &&
          Number(data.total_realized_pl) === 0
        ) {
          setState({ status: "empty", data: null });
        } else {
          setState({ status: "ready", data });
        }
      })
      .catch(() => {
        if (ac.signal.aborted) return;
        setState({ status: "empty", data: null });
      });
    return () => ac.abort();
  }, []);

  if (state.status !== "ready") return null;
  const s = state.data;
  const unreal = Number(s.total_unrealized_pl);
  const real = Number(s.total_realized_pl);
  const totalPl = unreal + real;
  const dir: "up" | "down" | "flat" =
    totalPl > 0 ? "up" : totalPl < 0 ? "down" : "flat";
  const tone = {
    up: "text-emerald-600 dark:text-emerald-400",
    down: "text-rose-600 dark:text-rose-400",
    flat: "text-zinc-500 dark:text-zinc-400",
  }[dir];

  return (
    <Link
      to="/portfolio"
      className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-zinc-200 bg-white px-4 py-3 hover:border-indigo-300 hover:shadow-sm dark:border-zinc-800 dark:bg-zinc-950 dark:hover:border-indigo-700"
    >
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-md bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
          <Briefcase className="h-4 w-4" />
        </div>
        <div>
          <div className="text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Portfolio
          </div>
          <div className="text-lg font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
            {fmtUSD(Number(s.total_current_value))}
          </div>
        </div>
      </div>
      <dl className="flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm">
        <div className="flex flex-col">
          <dt className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Unrealized
          </dt>
          <dd className={`font-mono tabular-nums ${tone}`}>
            {unreal > 0 ? "+" : ""}
            {fmtUSD(unreal)}
            {s.total_unrealized_pl_pct !== null && (
              <span className="ml-1 text-[11px]">
                ({Number(s.total_unrealized_pl_pct) > 0 ? "+" : ""}
                {Number(s.total_unrealized_pl_pct).toFixed(2)}%)
              </span>
            )}
          </dd>
        </div>
        <div className="flex flex-col">
          <dt className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Realized
          </dt>
          <dd className={`font-mono tabular-nums ${tone}`}>
            {real > 0 ? "+" : ""}
            {fmtUSD(real)}
          </dd>
        </div>
        <div className="flex flex-col">
          <dt className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Total P&amp;L
          </dt>
          <dd className={`font-mono tabular-nums font-semibold ${tone}`}>
            {dir === "up" && <ArrowUpRight className="mr-0.5 inline h-3 w-3" />}
            {dir === "down" && <ArrowDownRight className="mr-0.5 inline h-3 w-3" />}
            {dir === "flat" && <Minus className="mr-0.5 inline h-3 w-3" />}
            {totalPl > 0 ? "+" : ""}
            {fmtUSD(totalPl)}
          </dd>
        </div>
        <div className="flex flex-col">
          <dt className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Open positions
          </dt>
          <dd className="font-mono tabular-nums text-zinc-700 dark:text-zinc-300">
            {s.open_positions}
          </dd>
        </div>
      </dl>
    </Link>
  );
}

function fmtUSD(n: number): string {
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}
