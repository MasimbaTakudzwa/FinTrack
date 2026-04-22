import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDownRight,
  ArrowUpRight,
  LineChart as LineChartIcon,
  RefreshCw,
} from "lucide-react";
import {
  type MacroDataPoint,
  type MacroIndicator,
  type MacroSeries,
  getMacroSeries,
  listMacroIndicators,
} from "../api/client";
import { MacroLineChart } from "../components/MacroLineChart";
import { useResolvedTheme } from "../stores/useSettings";

// Upper bound on points per series — most FRED series in our seed set are
// monthly or quarterly, so even multi-decade spans come in under 1k points.
// CPIAUCSL goes back to 1947 → ~950 monthly points by 2026.
const MAX_POINTS = 2000;

interface SeriesState {
  loading: boolean;
  series: MacroSeries | null;
  error: string | null;
}

const EMPTY_SERIES_STATE: SeriesState = {
  loading: true,
  series: null,
  error: null,
};

/**
 * Macro page — browse FRED economic indicators.
 *
 * Left sidebar: list of active indicators. Right panel: selected indicator's
 * full history as a line chart plus a stats grid (latest / previous / change
 * vs. previous / change vs. series start).
 *
 * When no FRED_API_KEY is configured, the backend seeds indicators but never
 * ingests data, so the series endpoint returns an empty list. We surface that
 * with a friendly call-to-action pointing at `/settings`.
 */
export function Macro() {
  const [indicators, setIndicators] = useState<MacroIndicator[]>([]);
  const [indicatorsError, setIndicatorsError] = useState<string | null>(null);
  const [indicatorsLoading, setIndicatorsLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [seriesState, setSeriesState] =
    useState<SeriesState>(EMPTY_SERIES_STATE);
  const [tick, setTick] = useState(0);

  const dark = useResolvedTheme() === "dark";

  // Load the indicator list once (refresh bumps `tick`).
  useEffect(() => {
    const controller = new AbortController();
    listMacroIndicators({ signal: controller.signal })
      .then((list) => {
        const sorted = [...list].sort((a, b) =>
          a.series_id.localeCompare(b.series_id),
        );
        setIndicators(sorted);
        setIndicatorsError(null);
        setIndicatorsLoading(false);
        setSelected((prev) => {
          if (prev && sorted.some((i) => i.series_id === prev)) return prev;
          return sorted[0]?.series_id ?? null;
        });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setIndicators([]);
        setIndicatorsError(
          err instanceof Error ? err.message : String(err),
        );
        setIndicatorsLoading(false);
      });
    return () => controller.abort();
  }, [tick]);

  // Load the selected series whenever the selection or the refresh tick changes.
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    getMacroSeries(selected, { limit: MAX_POINTS, signal: controller.signal })
      .then((series) => {
        setSeriesState({ loading: false, series, error: null });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setSeriesState({
          loading: false,
          series: null,
          error: err instanceof Error ? err.message : String(err),
        });
      });
    return () => controller.abort();
  }, [selected, tick]);

  const refresh = () => {
    setIndicatorsLoading(true);
    setSeriesState((s) => ({ ...s, loading: true }));
    setTick((t) => t + 1);
  };

  const selectedIndicator = useMemo(
    () => indicators.find((i) => i.series_id === selected) ?? null,
    [indicators, selected],
  );

  return (
    <div className="p-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            Macro indicators
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {indicatorsLoading
              ? "Loading…"
              : `${indicators.length} FRED series · click to explore`}
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={indicatorsLoading || seriesState.loading}
          className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${indicatorsLoading || seriesState.loading ? "animate-spin" : ""}`}
          />
          Refresh
        </button>
      </div>

      {indicatorsError && (
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          {indicatorsError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[18rem_1fr]">
        <IndicatorList
          indicators={indicators}
          selected={selected}
          loading={indicatorsLoading}
          onSelect={setSelected}
        />
        <SeriesPanel
          indicator={selectedIndicator}
          state={seriesState}
          dark={dark}
        />
      </div>
    </div>
  );
}

/* ---------------- IndicatorList ---------------- */

function IndicatorList({
  indicators,
  selected,
  loading,
  onSelect,
}: {
  indicators: MacroIndicator[];
  selected: string | null;
  loading: boolean;
  onSelect: (seriesId: string) => void;
}) {
  if (loading && indicators.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-200 bg-white p-4 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
        Loading indicators…
      </div>
    );
  }

  if (indicators.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-200 bg-white p-4 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
        No indicators configured.
      </div>
    );
  }

  return (
    <nav className="rounded-lg border border-zinc-200 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-950">
      <h3 className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        FRED series
      </h3>
      <ul className="mt-1 space-y-0.5">
        {indicators.map((i) => {
          const active = i.series_id === selected;
          return (
            <li key={i.series_id}>
              <button
                type="button"
                onClick={() => onSelect(i.series_id)}
                className={
                  active
                    ? "w-full rounded-md bg-indigo-50 px-2 py-2 text-left transition-colors dark:bg-indigo-950/50"
                    : "w-full rounded-md px-2 py-2 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-900"
                }
              >
                <div
                  className={
                    active
                      ? "text-sm font-semibold text-indigo-700 dark:text-indigo-300"
                      : "text-sm font-semibold text-zinc-900 dark:text-zinc-100"
                  }
                >
                  {i.series_id}
                </div>
                <div className="mt-0.5 truncate text-xs text-zinc-500 dark:text-zinc-400">
                  {i.name}
                </div>
                {i.frequency && (
                  <div className="mt-1 text-[10px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
                    {i.frequency}
                    {i.units ? ` · ${i.units}` : ""}
                  </div>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/* ---------------- SeriesPanel ---------------- */

function SeriesPanel({
  indicator,
  state,
  dark,
}: {
  indicator: MacroIndicator | null;
  state: SeriesState;
  dark: boolean;
}) {
  if (!indicator) {
    return (
      <div className="rounded-lg border border-zinc-200 bg-white p-8 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
        Select a series on the left to explore.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h3 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
              {indicator.name}
            </h3>
            <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
              <span className="font-mono font-semibold">
                {indicator.series_id}
              </span>
              {indicator.frequency ? ` · ${indicator.frequency}` : ""}
              {indicator.units ? ` · ${indicator.units}` : ""}
            </p>
          </div>
          {state.series && (
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              {state.series.count.toLocaleString()} observations
            </p>
          )}
        </div>
        {indicator.description && (
          <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-300">
            {indicator.description}
          </p>
        )}
      </div>

      {state.error ? (
        <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          {state.error}
        </div>
      ) : state.loading && !state.series ? (
        <div className="rounded-lg border border-zinc-200 bg-white p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
          Loading series…
        </div>
      ) : state.series && state.series.points.length > 0 ? (
        <>
          <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
            <MacroLineChart
              points={state.series.points}
              dark={dark}
              height={380}
            />
          </div>
          <StatsGrid points={state.series.points} units={indicator.units} />
        </>
      ) : (
        <NoDataHint indicator={indicator} />
      )}
    </div>
  );
}

/* ---------------- NoDataHint ---------------- */

function NoDataHint({ indicator }: { indicator: MacroIndicator }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-8 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="mx-auto flex max-w-md flex-col items-center text-center">
        <div className="rounded-full bg-indigo-50 p-3 dark:bg-indigo-950/50">
          <LineChartIcon className="h-6 w-6 text-indigo-600 dark:text-indigo-400" />
        </div>
        <h4 className="mt-4 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          No data yet for {indicator.series_id}
        </h4>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          FRED data is fetched daily when a free API key is configured. Add one
          in settings and the next scheduled run will backfill this series.
        </p>
        <Link
          to="/settings"
          className="mt-4 inline-flex items-center gap-2 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500"
        >
          Add FRED API key
        </Link>
      </div>
    </div>
  );
}

/* ---------------- StatsGrid ---------------- */

interface Stats {
  latest: MacroDataPoint;
  previous: MacroDataPoint | null;
  earliest: MacroDataPoint;
  changeVsPrevAbs: number | null;
  changeVsPrevPct: number | null;
  changeVsStartPct: number | null;
}

function summarise(points: MacroDataPoint[]): Stats | null {
  if (points.length === 0) return null;
  const latest = points[points.length - 1];
  const earliest = points[0];
  const previous = points.length >= 2 ? points[points.length - 2] : null;
  const latestV = Number(latest.value);
  const prevV = previous ? Number(previous.value) : null;
  const earliestV = Number(earliest.value);

  const changeVsPrevAbs =
    prevV !== null && Number.isFinite(prevV) ? latestV - prevV : null;
  const changeVsPrevPct =
    prevV !== null && Number.isFinite(prevV) && prevV !== 0
      ? ((latestV - prevV) / prevV) * 100
      : null;
  const changeVsStartPct =
    Number.isFinite(earliestV) && earliestV !== 0
      ? ((latestV - earliestV) / earliestV) * 100
      : null;

  return {
    latest,
    previous,
    earliest,
    changeVsPrevAbs,
    changeVsPrevPct,
    changeVsStartPct,
  };
}

function StatsGrid({
  points,
  units,
}: {
  points: MacroDataPoint[];
  units: string | null;
}) {
  const stats = useMemo(() => summarise(points), [points]);
  if (!stats) return null;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCell
        label="Latest"
        value={fmtValue(Number(stats.latest.value), units)}
        sublabel={fmtDate(stats.latest.date)}
      />
      <StatCell
        label="Previous"
        value={
          stats.previous
            ? fmtValue(Number(stats.previous.value), units)
            : "—"
        }
        sublabel={stats.previous ? fmtDate(stats.previous.date) : undefined}
      />
      <StatCell
        label="vs. previous"
        value={
          stats.changeVsPrevAbs === null
            ? "—"
            : `${stats.changeVsPrevAbs > 0 ? "+" : ""}${fmtValue(stats.changeVsPrevAbs, units)}`
        }
        sublabel={
          stats.changeVsPrevPct === null
            ? undefined
            : fmtPct(stats.changeVsPrevPct)
        }
        tone={changeTone(stats.changeVsPrevAbs)}
      />
      <StatCell
        label={`vs. ${fmtDate(stats.earliest.date)}`}
        value={
          stats.changeVsStartPct === null ? "—" : fmtPct(stats.changeVsStartPct)
        }
        sublabel={`from ${fmtValue(Number(stats.earliest.value), units)}`}
        tone={changeTone(stats.changeVsStartPct)}
      />
    </div>
  );
}

function StatCell({
  label,
  value,
  sublabel,
  tone,
}: {
  label: string;
  value: string;
  sublabel?: string;
  tone?: "up" | "down" | "neutral";
}) {
  const valueCls =
    tone === "up"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "down"
        ? "text-rose-600 dark:text-rose-400"
        : "text-zinc-900 dark:text-zinc-100";
  const Icon =
    tone === "up" ? ArrowUpRight : tone === "down" ? ArrowDownRight : null;
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </div>
      <div
        className={`mt-1 inline-flex items-center gap-1 text-xl font-semibold tabular-nums ${valueCls}`}
      >
        {Icon && <Icon className="h-4 w-4" />}
        {value}
      </div>
      {sublabel && (
        <div className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
          {sublabel}
        </div>
      )}
    </div>
  );
}

/* ---------------- formatting helpers ---------------- */

function changeTone(n: number | null): "up" | "down" | "neutral" {
  if (n === null || !Number.isFinite(n) || n === 0) return "neutral";
  return n > 0 ? "up" : "down";
}

function fmtValue(n: number, units: string | null): string {
  if (!Number.isFinite(n)) return "—";
  const isPercent = units ? /percent|%/i.test(units) : false;
  const body =
    Math.abs(n) >= 1000
      ? n.toLocaleString(undefined, { maximumFractionDigits: 2 })
      : Math.abs(n) >= 1
        ? n.toFixed(2)
        : n.toFixed(4);
  return isPercent ? `${body}%` : body;
}

function fmtPct(pct: number): string {
  if (!Number.isFinite(pct)) return "—";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

function fmtDate(iso: string): string {
  // `iso` is "YYYY-MM-DD". Parse as UTC midnight so local timezone quirks don't
  // bump the display date off by a day.
  const ms = Date.parse(`${iso}T00:00:00Z`);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
