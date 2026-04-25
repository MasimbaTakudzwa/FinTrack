import { useEffect, useMemo, useState } from "react";
import { Activity, Loader2 } from "lucide-react";
import {
  ApiError,
  getCorrelations,
  getDefaultWatchlistCorrelations,
  type CorrelationCell,
  type CorrelationMatrix,
  listAssets,
} from "../api/client";

type State =
  | { status: "loading"; matrix: null; error: null }
  | { status: "ready"; matrix: CorrelationMatrix; error: null }
  | { status: "empty"; matrix: null; error: null }
  | { status: "error"; matrix: null; error: string };

const INITIAL: State = { status: "loading", matrix: null, error: null };

const LOOKBACK_OPTIONS: { label: string; value: number }[] = [
  { label: "30d", value: 30 },
  { label: "90d", value: 90 },
  { label: "180d", value: 180 },
  { label: "1y", value: 365 },
];

interface Props {
  /**
   * When true, the panel pulls from the user's default watchlist via
   * the dedicated endpoint. When false, it falls back to "every active
   * asset" via the explicit-symbols endpoint after a separate
   * /api/assets/ round-trip.
   */
  preferDefaultWatchlist?: boolean;
}

/**
 * Pairwise correlation heatmap. Rendered on the Market page as the
 * "diversification snapshot" panel — answers "which of my tracked
 * assets actually move together over the last N days?" using Pearson
 * correlations on daily log-returns.
 *
 * Falls back gracefully when there's no default watchlist (404 from
 * the convenience endpoint) by listing every active asset and asking
 * for the explicit-symbol matrix instead. Cells below the server's
 * `min_overlap_days` threshold are visually muted.
 */
export function CorrelationHeatmap({ preferDefaultWatchlist = true }: Props) {
  const [lookback, setLookback] = useState<number>(90);
  const [state, setState] = useState<State>(INITIAL);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const ac = new AbortController();
    let cancelled = false;

    (async () => {
      try {
        let matrix: CorrelationMatrix;
        if (preferDefaultWatchlist) {
          try {
            matrix = await getDefaultWatchlistCorrelations({
              lookbackDays: lookback,
              signal: ac.signal,
            });
          } catch (err) {
            if (err instanceof ApiError && err.status === 404) {
              const assets = await listAssets({
                activeOnly: true,
                signal: ac.signal,
              });
              if (assets.length === 0) {
                if (!cancelled) {
                  setState({ status: "empty", matrix: null, error: null });
                }
                return;
              }
              matrix = await getCorrelations({
                symbols: assets.map((a) => a.symbol),
                lookbackDays: lookback,
                signal: ac.signal,
              });
            } else {
              throw err;
            }
          }
        } else {
          const assets = await listAssets({
            activeOnly: true,
            signal: ac.signal,
          });
          if (assets.length === 0) {
            if (!cancelled) {
              setState({ status: "empty", matrix: null, error: null });
            }
            return;
          }
          matrix = await getCorrelations({
            symbols: assets.map((a) => a.symbol),
            lookbackDays: lookback,
            signal: ac.signal,
          });
        }
        if (cancelled) return;
        if (matrix.symbols.length === 0) {
          setState({ status: "empty", matrix: null, error: null });
        } else {
          setState({ status: "ready", matrix, error: null });
        }
      } catch (err) {
        if (cancelled || ac.signal.aborted) return;
        setState({
          status: "error",
          matrix: null,
          error: err instanceof Error ? err.message : "Failed to load correlations",
        });
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [preferDefaultWatchlist, lookback, tick]);

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          <Activity className="h-3.5 w-3.5 text-indigo-500" />
          Correlation matrix
          <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-normal normal-case tracking-normal text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
            daily log-returns
          </span>
        </h3>
        <div className="flex items-center gap-2">
          <LookbackPicker value={lookback} onChange={setLookback} />
          <button
            type="button"
            onClick={() => setTick((t) => t + 1)}
            className="rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Refresh
          </button>
        </div>
      </header>
      <Body state={state} />
    </section>
  );
}

function Body({ state }: { state: State }) {
  if (state.status === "loading") {
    return (
      <div className="flex items-center gap-2 py-6 text-sm text-zinc-500 dark:text-zinc-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        Computing correlations…
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

  if (state.status === "empty") {
    return (
      <div className="py-6 text-sm text-zinc-500 dark:text-zinc-400">
        Add a few assets and let the daily-bar job run for a while — the
        correlation heatmap needs at least a few days of overlapping
        returns to show meaningful structure.
      </div>
    );
  }

  return <Heatmap matrix={state.matrix} />;
}

function Heatmap({ matrix }: { matrix: CorrelationMatrix }) {
  // Build a lookup so we can render the lower-triangle cells by mirroring
  // the upper-triangle ones the server emitted.
  const cellMap = useMemo(() => {
    const m = new Map<string, CorrelationCell>();
    for (const c of matrix.cells) {
      m.set(`${c.symbol_a}|${c.symbol_b}`, c);
    }
    return m;
  }, [matrix]);

  const lookup = (a: string, b: string): CorrelationCell | undefined => {
    return cellMap.get(`${a}|${b}`) ?? cellMap.get(`${b}|${a}`);
  };

  return (
    <div className="overflow-x-auto">
      <table className="text-[11px]">
        <thead>
          <tr>
            <th className="px-1.5 py-1" />
            {matrix.symbols.map((s) => (
              <th
                key={s}
                className="px-1.5 py-1 text-left font-mono font-semibold text-zinc-700 dark:text-zinc-300"
                title={s}
              >
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.symbols.map((rowSym) => (
            <tr key={rowSym}>
              <th
                scope="row"
                className="pr-2 text-right font-mono font-semibold text-zinc-700 dark:text-zinc-300"
              >
                {rowSym}
              </th>
              {matrix.symbols.map((colSym) => {
                const cell = lookup(rowSym, colSym);
                return (
                  <td key={colSym} className="px-0.5 py-0.5">
                    <Cell
                      cell={cell}
                      isDiagonal={rowSym === colSym}
                      minOverlap={matrix.min_overlap_days}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-[11px] text-zinc-500 dark:text-zinc-400">
        Pearson correlation on daily log-returns over the last {matrix.lookback_days}
        {" "}days. Diagonals are 1.0 by convention; cells with under
        {" "}{matrix.min_overlap_days} overlapping return-days are dimmed.
      </p>
    </div>
  );
}

function Cell({
  cell,
  isDiagonal,
  minOverlap,
}: {
  cell: CorrelationCell | undefined;
  isDiagonal: boolean;
  minOverlap: number;
}) {
  if (!cell) {
    return (
      <div className="flex h-7 w-12 items-center justify-center rounded bg-zinc-100 text-[10px] text-zinc-300 dark:bg-zinc-800 dark:text-zinc-700">
        —
      </div>
    );
  }
  const value = cell.coefficient;
  const dim = !isDiagonal && cell.overlap < minOverlap;
  return (
    <div
      title={`r = ${value.toFixed(3)} · ${cell.overlap} day${cell.overlap === 1 ? "" : "s"}`}
      style={{
        background: heatColor(value, dim),
        color: dim ? undefined : labelColor(value),
      }}
      className={`flex h-7 w-12 items-center justify-center rounded text-[10px] font-mono tabular-nums ${
        dim ? "text-zinc-400 dark:text-zinc-500" : ""
      }`}
    >
      {value >= 0.995 ? "1.00" : value.toFixed(2)}
    </div>
  );
}

/**
 * Diverging color scale: rose for strong negative correlation, neutral
 * grey at zero, emerald for strong positive correlation. Dimmed cells
 * shift toward zinc to visually de-emphasise low-confidence numbers
 * without collapsing them to white (still readable in dark mode).
 */
function heatColor(value: number, dim: boolean): string {
  if (dim) {
    return "rgba(161, 161, 170, 0.18)";
  }
  const clamped = Math.max(-1, Math.min(1, value));
  const intensity = Math.abs(clamped);
  if (clamped >= 0) {
    // Emerald-500 #10b981 with alpha = 0.15..0.85
    const alpha = 0.15 + intensity * 0.7;
    return `rgba(16, 185, 129, ${alpha.toFixed(2)})`;
  }
  // Rose-500 #f43f5e
  const alpha = 0.15 + intensity * 0.7;
  return `rgba(244, 63, 94, ${alpha.toFixed(2)})`;
}

function labelColor(value: number): string {
  // Switch the cell text to white once the background is dark enough
  // for the default zinc text to lose contrast.
  return Math.abs(value) > 0.55 ? "#ffffff" : "#27272a";
}

function LookbackPicker({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="inline-flex rounded border border-zinc-200 bg-white p-0.5 dark:border-zinc-700 dark:bg-zinc-900">
      {LOOKBACK_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`rounded px-2 py-0.5 text-[11px] font-medium ${
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
