import { useEffect, useState } from "react";
import { Loader2, TrendingUp } from "lucide-react";
import { getVolatility, type VolatilityReport } from "../api/client";

type State =
  | { status: "loading"; data: null; error: null }
  | { status: "ready"; data: VolatilityReport; error: null }
  | { status: "error"; data: null; error: string };

const INITIAL: State = { status: "loading", data: null, error: null };

interface Props {
  symbol: string;
  /** Window length for the realized-vol stdev (default 30 days). */
  lookbackDays?: number;
}

/**
 * Realized + EWMA-forecast volatility headline for one asset.
 *
 * Renders the standard "what move should I expect tomorrow?" answer:
 * realized vol (annualized %), EWMA next-day forecast (% + price-space
 * band), and the ±1σ window centred on the last close. Pairs with the
 * ForecastAccuracyPanel as the "uncertainty quantification" track on
 * AssetDetail.
 *
 * Sidesteps the React 19 `react-hooks/set-state-in-effect` rule the same
 * way the accuracy panel does — initial INITIAL.status='loading' drives
 * the spinner; subsequent fetches only update via .then handlers.
 */
export function VolatilityPanel({ symbol, lookbackDays = 30 }: Props) {
  const [state, setState] = useState<State>(INITIAL);

  useEffect(() => {
    const ac = new AbortController();
    getVolatility(symbol, { lookbackDays, signal: ac.signal })
      .then((data) => setState({ status: "ready", data, error: null }))
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setState({
          status: "error",
          data: null,
          error: err instanceof Error ? err.message : "Failed to load volatility",
        });
      });
    return () => ac.abort();
  }, [symbol, lookbackDays]);

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          <TrendingUp className="h-4 w-4 text-amber-500" />
          Volatility
          <span className="text-xs font-normal text-zinc-500 dark:text-zinc-400">
            · last {lookbackDays}d
          </span>
        </h2>
      </header>
      <Body state={state} />
    </section>
  );
}

function Body({ state }: { state: State }) {
  if (state.status === "loading") {
    return (
      <div className="flex items-center gap-2 py-3 text-sm text-zinc-500 dark:text-zinc-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        Computing volatility…
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
        {state.error}
      </div>
    );
  }

  const { data } = state;

  if (data.realized_vol_annualized === null || data.last_close === null) {
    return (
      <div className="py-3 text-sm text-zinc-500 dark:text-zinc-400">
        Need at least a few days of daily-bar history before volatility
        metrics are meaningful — wait for the daily ingest to backfill,
        then refresh.
      </div>
    );
  }

  const annualPct = data.realized_vol_annualized * 100;
  const dailyPct =
    data.realized_vol_daily !== null ? data.realized_vol_daily * 100 : null;
  const ewmaPct =
    data.ewma_next_day_vol !== null ? data.ewma_next_day_vol * 100 : null;

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="text-2xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
            {annualPct.toFixed(1)}%
          </div>
          <div className="text-xs uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            annualized realized vol
          </div>
        </div>
        {dailyPct !== null && (
          <div className="text-right">
            <div className="text-base font-semibold tabular-nums text-zinc-700 dark:text-zinc-300">
              {dailyPct.toFixed(2)}%
            </div>
            <div className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              daily
            </div>
          </div>
        )}
      </div>

      {data.expected_move_low !== null &&
        data.expected_move_high !== null &&
        ewmaPct !== null && (
          <div className="rounded-md border border-amber-200/60 bg-amber-50/60 p-3 dark:border-amber-900/40 dark:bg-amber-950/30">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
              Expected next-day move (±1σ)
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-base font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                ${fmtPrice(data.expected_move_low)} – ${fmtPrice(data.expected_move_high)}
              </span>
              <span className="text-xs text-zinc-500 dark:text-zinc-400">
                ±{ewmaPct.toFixed(2)}% (EWMA)
              </span>
            </div>
          </div>
        )}

      <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
        Annualised stdev of daily log-returns over the last {data.returns_used}{" "}
        return-day{data.returns_used === 1 ? "" : "s"}; next-day band uses
        RiskMetrics-style EWMA (λ = 0.94).
      </p>
    </div>
  );
}

function fmtPrice(n: number): string {
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(2);
  return n.toFixed(4);
}
