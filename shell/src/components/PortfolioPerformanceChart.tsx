import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from "lightweight-charts";
import { Loader2 } from "lucide-react";
import {
  getPortfolioPerformance,
  type PortfolioPerformance,
} from "../api/client";
import { useResolvedTheme } from "../stores/useSettings";

type State =
  | { status: "loading"; data: null; error: null }
  | { status: "ready"; data: PortfolioPerformance; error: null }
  | { status: "error"; data: null; error: string };

const INITIAL: State = { status: "loading", data: null, error: null };

const LOOKBACK_OPTIONS: { value: number; label: string }[] = [
  { value: 30, label: "30d" },
  { value: 90, label: "90d" },
  { value: 180, label: "180d" },
  { value: 365, label: "1y" },
  { value: 1825, label: "5y" },
];

interface Palette {
  bg: string;
  text: string;
  grid: string;
  border: string;
  value: string;
  costBasis: string;
}

function palette(theme: "light" | "dark"): Palette {
  return theme === "dark"
    ? {
        bg: "#0a0a0a",
        text: "#d4d4d8",
        grid: "#27272a",
        border: "#3f3f46",
        value: "#10b981",
        costBasis: "#a1a1aa",
      }
    : {
        bg: "#ffffff",
        text: "#3f3f46",
        grid: "#e4e4e7",
        border: "#d4d4d8",
        value: "#059669",
        costBasis: "#71717a",
      };
}

interface Props {
  /** Bumps after every transaction add/delete so the chart re-fetches. */
  refreshTick: number;
}

/**
 * Daily portfolio value chart over a configurable lookback. Two lines:
 * the green "value" curve (sum of position qty × close per day) and a
 * faint dashed "cost basis" reference (sum of qty × avg_cost). The gap
 * between them is the unrealized P&L.
 *
 * The chart-bound div mounts once and persists across state changes;
 * loading / empty / error states render as overlays so the chart
 * doesn't get torn down on every refresh.
 */
export function PortfolioPerformanceChart({ refreshTick }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const valueRef = useRef<ISeriesApi<"Line"> | null>(null);
  const costRef = useRef<ISeriesApi<"Line"> | null>(null);
  const theme = useResolvedTheme();

  const [lookback, setLookback] = useState<number>(90);
  const [state, setState] = useState<State>(INITIAL);

  // Fetch on mount, lookback change, or external refresh tick.
  useEffect(() => {
    const ac = new AbortController();
    getPortfolioPerformance({ lookbackDays: lookback, signal: ac.signal })
      .then((data) => setState({ status: "ready", data, error: null }))
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setState({
          status: "error",
          data: null,
          error: err instanceof Error ? err.message : "Failed to load",
        });
      });
    return () => ac.abort();
  }, [lookback, refreshTick]);

  // Chart lifecycle — recreate on theme change. The chart-bound div is
  // always mounted (see render below) so this effect's containerRef
  // reads cleanly.
  useEffect(() => {
    if (!containerRef.current) return undefined;
    const p = palette(theme);
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: p.bg },
        textColor: p.text,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: p.grid },
        horzLines: { color: p.grid },
      },
      timeScale: {
        timeVisible: false,
        secondsVisible: false,
        borderColor: p.border,
      },
      rightPriceScale: {
        borderColor: p.border,
        scaleMargins: { top: 0.15, bottom: 0.15 },
      },
      crosshair: { mode: 1 },
    });
    chartRef.current = chart;

    valueRef.current = chart.addSeries(LineSeries, {
      color: p.value,
      lineWidth: 2,
      priceLineVisible: false,
      crosshairMarkerVisible: true,
      lastValueVisible: true,
    });
    costRef.current = chart.addSeries(LineSeries, {
      color: p.costBasis,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      lastValueVisible: true,
    });

    return () => {
      chart.remove();
      chartRef.current = null;
      valueRef.current = null;
      costRef.current = null;
    };
  }, [theme]);

  // Data effect — push points whenever a fresh fetch lands.
  useEffect(() => {
    const v = valueRef.current;
    const c = costRef.current;
    if (!v || !c || state.status !== "ready") return;
    const points = state.data.points;
    if (points.length === 0) {
      v.setData([]);
      c.setData([]);
      return;
    }
    v.setData(
      points.map(
        (p) =>
          ({
            time: dateToTs(p.date),
            value: Number(p.value),
          }) as LineData<UTCTimestamp>,
      ),
    );
    c.setData(
      points.map(
        (p) =>
          ({
            time: dateToTs(p.date),
            value: Number(p.cost_basis),
          }) as LineData<UTCTimestamp>,
      ),
    );
    chartRef.current?.timeScale().fitContent();
  }, [state]);

  const empty =
    state.status === "ready" && state.data.points.length === 0;
  const loading = state.status === "loading";
  const error = state.status === "error" ? state.error : null;

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Performance
          <span className="ml-2 inline-flex items-center gap-1 text-[10px] font-normal text-zinc-400 dark:text-zinc-500">
            <span
              className="h-2 w-2 rounded-full"
              style={{ background: palette(theme).value }}
            />
            value
            <span
              className="ml-2 h-2 w-2 rounded-full"
              style={{ background: palette(theme).costBasis }}
            />
            cost basis
          </span>
        </h3>
        <LookbackPicker value={lookback} onChange={setLookback} />
      </header>
      <div className="relative h-72 w-full">
        <div ref={containerRef} className="h-full w-full" />
        {(loading || empty || error) && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/60 text-sm text-zinc-500 dark:bg-zinc-950/60 dark:text-zinc-400">
            {loading ? (
              <span className="inline-flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                Computing performance…
              </span>
            ) : error ? (
              <span className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
                {error}
              </span>
            ) : (
              <span>No transactions or daily-close data inside the window yet.</span>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function dateToTs(iso: string): UTCTimestamp {
  return (Date.parse(`${iso}T00:00:00Z`) / 1000) as UTCTimestamp;
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
