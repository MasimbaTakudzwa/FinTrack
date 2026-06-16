import { useEffect, useState } from "react";
import { Activity, Loader2, Target } from "lucide-react";
import {
  getForecastAccuracy,
  type EngineAccuracyEntry,
  type ForecastAccuracyReport,
} from "../api/client";

type State =
  | { status: "loading"; data: null; error: null }
  | { status: "ready"; data: ForecastAccuracyReport; error: null }
  | { status: "error"; data: null; error: string };

const INITIAL: State = { status: "loading", data: null, error: null };

interface Props {
  symbol: string;
  /** Window length for rolling metrics (defaults to 30 days). */
  days?: number;
  /**
   * Increments whenever the parent retrains. We don't take a `key` here
   * because that would re-mount the panel and lose its loaded data on
   * every retrain — instead we re-fetch in place, which keeps the
   * previous numbers visible until the new ones land.
   */
  refreshTick?: number;
}

/**
 * "How accurate has the forecaster been?" panel for AssetDetail.
 *
 * Renders the per-engine breakdown of MAPE / RMSE / directional accuracy
 * with the best engine pinned at the top, plus a small headline showing
 * the overall rollup. Designed to answer "should I switch engines?" at
 * a glance — the user can see whether SARIMAX or Holt-Winters has been
 * fitting their data better and act on it via Settings → ML controls.
 */
export function ForecastAccuracyPanel({ symbol, days = 30, refreshTick = 0 }: Props) {
  const [state, setState] = useState<State>(INITIAL);

  // React 19's `react-hooks/set-state-in-effect` rule forbids synchronous
  // setState inside an effect body. We rely on this in our favour: when
  // `refreshTick` bumps after a retrain, the previous "ready" state stays
  // visible until the .then handler swaps it for the freshly-computed
  // metrics, which feels nicer than a flicker back to the spinner.
  useEffect(() => {
    const ac = new AbortController();
    getForecastAccuracy(symbol, { days, signal: ac.signal })
      .then((data) => setState({ status: "ready", data, error: null }))
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setState({
          status: "error",
          data: null,
          error: err instanceof Error ? err.message : "Failed to load accuracy",
        });
      });
    return () => ac.abort();
  }, [symbol, days, refreshTick]);

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          <Target className="h-4 w-4 text-indigo-500" />
          Forecast accuracy
          <span className="text-xs font-normal text-zinc-500 dark:text-zinc-400">
            · last {days}d
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
        <span>Computing accuracy…</span>
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

  const { per_engine, overall, naive } = state.data;

  if (per_engine.length === 0) {
    return (
      <div className="py-3 text-sm text-zinc-500 dark:text-zinc-400">
        No forecasts have been generated for this asset yet — train one to
        start tracking accuracy.
      </div>
    );
  }

  const totalEvaluable = overall?.evaluable_points ?? 0;
  const totalSnapshots = overall?.snapshots ?? 0;

  return (
    <div className="space-y-3">
      {totalEvaluable === 0 ? (
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          {totalSnapshots} snapshot{totalSnapshots === 1 ? "" : "s"} on
          file — no forecast horizons have elapsed yet, so accuracy
          metrics will appear once the predicted dates pass.
        </p>
      ) : (
        <>
          <Headline overall={overall} />
          <BaselineVerdict overall={overall} naive={naive} />
        </>
      )}

      <div className="overflow-hidden rounded-md border border-zinc-200 dark:border-zinc-800">
        <table className="w-full text-left text-xs">
          <thead className="bg-zinc-50 text-[10px] uppercase tracking-wide text-zinc-500 dark:bg-zinc-950 dark:text-zinc-400">
            <tr>
              <th className="px-3 py-2 font-medium">Engine</th>
              <th className="px-3 py-2 text-right font-medium">MAPE</th>
              <th className="px-3 py-2 text-right font-medium">RMSE</th>
              <th className="px-3 py-2 text-right font-medium">Directional</th>
              <th className="px-3 py-2 text-right font-medium">N</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {per_engine.map((row, idx) => (
              <EngineRow key={row.engine} row={row} best={idx === 0 && row.mape !== null} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Headline({ overall }: { overall: EngineAccuracyEntry | null }) {
  if (!overall || overall.mape === null) return null;
  const dirText =
    overall.directional !== null
      ? `${(overall.directional * 100).toFixed(0)}% directional`
      : "—";
  return (
    <div className="flex items-baseline gap-2 text-zinc-900 dark:text-zinc-100">
      <Activity className="h-4 w-4 text-emerald-500" />
      <span className="text-2xl font-semibold tabular-nums">
        {overall.mape.toFixed(2)}%
      </span>
      <span className="text-xs uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        avg MAPE · {dirText} · {overall.evaluable_points} pt
        {overall.evaluable_points === 1 ? "" : "s"}
      </span>
    </div>
  );
}

/**
 * Honest yardstick: how the model's error compares to a naive "assume no
 * change" random-walk baseline over the same window. For a 2-week daily-close
 * forecast these are usually within a hair of each other — by design we say so
 * plainly rather than dressing up the number.
 */
function BaselineVerdict({
  overall,
  naive,
}: {
  overall: EngineAccuracyEntry | null;
  naive: EngineAccuracyEntry | null;
}) {
  if (!overall || overall.mape === null || !naive || naive.mape === null) {
    return null;
  }
  const model = overall.mape;
  const base = naive.mape;
  const beatsBy = base - model; // positive = model better
  const ratio = base > 0 ? model / base : 1;
  let verdict: string;
  let tone: string;
  if (ratio <= 0.9) {
    verdict = "beats a no-change guess";
    tone = "text-emerald-600 dark:text-emerald-400";
  } else if (ratio <= 1.1) {
    verdict = "about as accurate as assuming no change";
    tone = "text-zinc-500 dark:text-zinc-400";
  } else {
    verdict = "worse than just assuming no change";
    tone = "text-amber-600 dark:text-amber-400";
  }
  return (
    <p className="text-xs text-zinc-500 dark:text-zinc-400">
      vs naive baseline{" "}
      <span className="tabular-nums text-zinc-700 dark:text-zinc-300">
        {base.toFixed(2)}%
      </span>{" "}
      — <span className={tone}>{verdict}</span>
      {Math.abs(beatsBy) >= 0.01 ? (
        <span className="tabular-nums">
          {" "}
          ({beatsBy > 0 ? "−" : "+"}
          {Math.abs(beatsBy).toFixed(2)} pts)
        </span>
      ) : null}
    </p>
  );
}

function EngineRow({
  row,
  best,
}: {
  row: EngineAccuracyEntry;
  best: boolean;
}) {
  return (
    <tr
      className={
        best
          ? "bg-emerald-50/40 dark:bg-emerald-900/10"
          : "bg-white dark:bg-zinc-900"
      }
    >
      <td className="px-3 py-2">
        <div className="flex items-center gap-2 font-medium text-zinc-800 dark:text-zinc-200">
          {row.engine}
          {best && (
            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
              Best
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-zinc-700 dark:text-zinc-300">
        {row.mape !== null ? `${row.mape.toFixed(2)}%` : "—"}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-zinc-700 dark:text-zinc-300">
        {row.rmse !== null ? row.rmse.toFixed(2) : "—"}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-zinc-700 dark:text-zinc-300">
        {row.directional !== null
          ? `${(row.directional * 100).toFixed(0)}%`
          : "—"}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-zinc-500 dark:text-zinc-400">
        {row.evaluable_points} / {row.snapshots}
      </td>
    </tr>
  );
}
