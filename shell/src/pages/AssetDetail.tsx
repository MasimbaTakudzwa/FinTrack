import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Activity,
  ArrowDownRight,
  ArrowLeft,
  ArrowUpRight,
  Bell,
  Eye,
  EyeOff,
  Loader2,
  Minus,
  MousePointerClick,
  Newspaper,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import {
  type Article,
  type Asset,
  ApiError,
  type Forecast,
  type PriceAlert,
  type PricePoint,
  type PriceSeries,
  getForecast,
  getPriceSeries,
  listAlerts,
  listAssets,
  listNews,
  retrainForecast,
} from "../api/client";
import { AlertCreateModal } from "../components/AlertCreateModal";
import { CandleChart } from "../components/CandleChart";
import { NewsList } from "../components/NewsList";
import { useResolvedTheme } from "../stores/useSettings";

interface State {
  asset: Asset | null;
  series: PriceSeries | null;
  loading: boolean;
  error: string | null;
  notFound: boolean;
}

const INITIAL: State = {
  asset: null,
  series: null,
  loading: true,
  error: null,
  notFound: false,
};

// Max bars to pull from the API. 5-min bars × ~42 hours worth of coverage
// for a brand-new install; settles higher as scheduler runs accumulate.
// yfinance 5-min history tops out ~60 days, so this is also a soft ceiling.
const MAX_BARS = 3000;

function fmtPrice(n: number): string {
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(2);
  return n.toFixed(4);
}

function fmtDelta(n: number): string {
  const sign = n > 0 ? "+" : "";
  if (Math.abs(n) >= 1000)
    return `${sign}${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  if (Math.abs(n) >= 1) return `${sign}${n.toFixed(2)}`;
  return `${sign}${n.toFixed(4)}`;
}

function fmtVolume(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`;
  return n.toLocaleString();
}

function fmtPct(pct: number): string {
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

function latestChange(points: PricePoint[]): number | null {
  if (points.length < 2) return null;
  const last = Number(points[points.length - 1].close);
  const prev = Number(points[points.length - 2].close);
  if (!prev) return null;
  return ((last - prev) / prev) * 100;
}

/**
 * SQLite returns naive datetimes — coerce to UTC for a consistent parse.
 * Used to turn ``timestamp`` strings on PricePoint into unix-millis.
 */
function parseBarMs(iso: string): number {
  const normalised = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return Date.parse(normalised);
}

function tone(dir: "up" | "down" | "flat"): string {
  return {
    up: "text-emerald-600 dark:text-emerald-400",
    down: "text-rose-600 dark:text-rose-400",
    flat: "text-zinc-500 dark:text-zinc-400",
  }[dir];
}

export function AssetDetail() {
  const { symbol } = useParams<{ symbol: string }>();
  const [state, setState] = useState<State>(INITIAL);
  const [tick, setTick] = useState(0);
  const resolved = useResolvedTheme();

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const [assets, series] = await Promise.all([
          listAssets({ activeOnly: false, signal: controller.signal }),
          getPriceSeries(symbol, { limit: MAX_BARS, signal: controller.signal }),
        ]);
        const asset = assets.find(
          (a) => a.symbol.toUpperCase() === symbol.toUpperCase(),
        );
        if (cancelled) return;
        if (!asset) {
          setState({ ...INITIAL, loading: false, notFound: true });
          return;
        }
        setState({
          asset,
          series,
          loading: false,
          error: null,
          notFound: false,
        });
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        setState({
          ...INITIAL,
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [symbol, tick]);

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    setTick((t) => t + 1);
  };

  if (!symbol) {
    return (
      <div className="p-6 text-sm text-zinc-500 dark:text-zinc-400">
        No symbol in URL.
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Dashboard
        </Link>
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

      {state.notFound && (
        <div className="rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-400">
          Unknown symbol <code className="font-mono text-zinc-700 dark:text-zinc-200">{symbol}</code>.
        </div>
      )}

      {state.error && !state.notFound && (
        <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          Failed to load: {state.error}
        </div>
      )}

      {state.asset && state.series && (
        // `key={symbol}` remounts the body on navigation so forecast / measure
        // / timeframe state doesn't leak across assets (matches the NewsPanel
        // pattern used inside).
        <AssetBody
          key={state.asset.symbol}
          asset={state.asset}
          series={state.series}
          dark={resolved === "dark"}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Timeframes
// ---------------------------------------------------------------------------

type TimeframeId = "1H" | "4H" | "1D" | "3D" | "1W" | "ALL";

interface Timeframe {
  id: TimeframeId;
  label: string;
  /** Window length in ms, or null for "all". */
  windowMs: number | null;
  title: string;
}

const TIMEFRAMES: Timeframe[] = [
  { id: "1H", label: "1H", windowMs: 60 * 60 * 1000, title: "Last 1 hour" },
  { id: "4H", label: "4H", windowMs: 4 * 60 * 60 * 1000, title: "Last 4 hours" },
  { id: "1D", label: "1D", windowMs: 24 * 60 * 60 * 1000, title: "Last 24 hours" },
  { id: "3D", label: "3D", windowMs: 3 * 24 * 60 * 60 * 1000, title: "Last 3 days" },
  { id: "1W", label: "1W", windowMs: 7 * 24 * 60 * 60 * 1000, title: "Last 7 days" },
  { id: "ALL", label: "All", windowMs: null, title: "All available bars" },
];

function sliceToTimeframe(points: PricePoint[], tf: Timeframe): PricePoint[] {
  if (!tf.windowMs || points.length === 0) return points;
  const lastMs = parseBarMs(points[points.length - 1].timestamp);
  const cutoff = lastMs - tf.windowMs;
  // Binary search would be faster but 3000 bars is fine with .findIndex.
  const firstIdx = points.findIndex((p) => parseBarMs(p.timestamp) >= cutoff);
  if (firstIdx <= 0) return points;
  return points.slice(firstIdx);
}

// ---------------------------------------------------------------------------
// Measurement
// ---------------------------------------------------------------------------

interface MeasurePoint {
  /** UTC seconds (the chart's Time type). */
  time: number;
  price: number;
}

interface MeasureState {
  first: MeasurePoint | null;
  second: MeasurePoint | null;
}

const MEASURE_EMPTY: MeasureState = { first: null, second: null };

function fmtDuration(ms: number): string {
  const s = Math.round(Math.abs(ms) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = m / 60;
  if (h < 24) return `${h.toFixed(h < 10 ? 1 : 0)}h`;
  const d = h / 24;
  return `${d.toFixed(d < 10 ? 1 : 0)}d`;
}

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------

type ForecastStatus = "loading" | "ready" | "not_trained" | "error";

interface ForecastState {
  data: Forecast | null;
  status: ForecastStatus;
  error: string | null;
  retraining: boolean;
}

const FORECAST_INITIAL: ForecastState = {
  data: null,
  status: "loading",
  error: null,
  retraining: false,
};

function AssetBody({
  asset,
  series,
  dark,
}: {
  asset: Asset;
  series: PriceSeries;
  dark: boolean;
}) {
  const [alertOpen, setAlertOpen] = useState(false);
  const [lastCreatedAlert, setLastCreatedAlert] = useState<PriceAlert | null>(
    null,
  );
  const [tfId, setTfId] = useState<TimeframeId>("1D");
  const [measure, setMeasure] = useState<MeasureState>(MEASURE_EMPTY);
  const [fc, setFc] = useState<ForecastState>(FORECAST_INITIAL);
  const [showForecast, setShowForecast] = useState(false);

  // Fetch the persisted forecast on mount. AssetBody is keyed on
  // ``asset.symbol`` by the parent, so this effect only fires once per mount
  // — initial state (``FORECAST_INITIAL.status = "loading"``) already drives
  // the loading UI until the .then/.catch handlers flip status.
  // 404 is the expected state for assets that haven't been trained yet —
  // surface as ``not_trained`` so the panel shows a "Train now" CTA instead
  // of an error.
  useEffect(() => {
    const controller = new AbortController();
    getForecast(asset.symbol, controller.signal)
      .then((data) => {
        setFc({ data, status: "ready", error: null, retraining: false });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) {
          setFc({ data: null, status: "not_trained", error: null, retraining: false });
          return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        setFc({ data: null, status: "error", error: msg, retraining: false });
      });
    return () => controller.abort();
  }, [asset.symbol]);

  const onRetrain = async () => {
    setFc((s) => ({ ...s, retraining: true, error: null }));
    try {
      const data = await retrainForecast(asset.symbol);
      setFc({ data, status: "ready", error: null, retraining: false });
      // If the user hit "Train now" from a cold state, show them the result
      // immediately — no point hiding what they just asked for.
      setShowForecast(true);
    } catch (err) {
      let msg = err instanceof Error ? err.message : String(err);
      if (err instanceof ApiError) {
        if (err.status === 422) {
          msg =
            "Not enough daily history yet. The daily-bar job needs " +
            "to run for a while before SARIMAX can fit.";
        }
      }
      setFc((s) => ({ ...s, retraining: false, error: msg }));
    }
  };

  const tf = TIMEFRAMES.find((t) => t.id === tfId) ?? TIMEFRAMES[TIMEFRAMES.length - 1];
  const visiblePoints = useMemo(
    () => sliceToTimeframe(series.points, tf),
    [series.points, tf],
  );

  const last = visiblePoints[visiblePoints.length - 1];
  const lastClose = last ? Number(last.close) : null;
  const changePct = latestChange(visiblePoints);
  const dir: "up" | "down" | "flat" =
    changePct === null ? "flat" : changePct > 0 ? "up" : changePct < 0 ? "down" : "flat";

  const onPickTf = (id: TimeframeId) => {
    setTfId(id);
    // Invalidating measurements on timeframe change avoids stale markers
    // that may not even be in the visible slice anymore.
    setMeasure(MEASURE_EMPTY);
  };

  const onChartClick = useCallback((p: MeasurePoint) => {
    setMeasure((m) => {
      if (m.first === null) return { first: p, second: null };
      if (m.second === null) return { ...m, second: p };
      // Third click → start a new measurement pair.
      return { first: p, second: null };
    });
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
              {asset.symbol}
            </h2>
            <span className="rounded bg-zinc-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
              {asset.asset_type}
            </span>
          </div>
          <p className="mt-0.5 text-sm text-zinc-500 dark:text-zinc-400">{asset.name}</p>
        </div>
        <div className="flex items-start gap-3">
          <button
            type="button"
            onClick={() => setAlertOpen(true)}
            className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-emerald-500/40 bg-emerald-500/5 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-500/10 dark:border-emerald-500/40 dark:text-emerald-300"
          >
            <Bell className="h-3.5 w-3.5" />
            Create alert
          </button>
          <div className="text-right">
            <div className="text-3xl font-semibold tracking-tight tabular-nums text-zinc-900 dark:text-zinc-100">
              {lastClose === null ? "—" : fmtPrice(lastClose)}
            </div>
            <div className={`mt-0.5 inline-flex items-center gap-1 text-sm font-semibold ${tone(dir)}`}>
              {dir === "up" && <ArrowUpRight className="h-4 w-4" />}
              {dir === "down" && <ArrowDownRight className="h-4 w-4" />}
              {dir === "flat" && <Minus className="h-4 w-4" />}
              {changePct === null ? "—" : fmtPct(changePct)}
            </div>
          </div>
        </div>
      </div>

      {lastCreatedAlert && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">
          Alert created: {lastCreatedAlert.symbol} {lastCreatedAlert.direction}{" "}
          {lastCreatedAlert.threshold}.{" "}
          <Link to="/alerts" className="font-medium underline">
            Manage alerts →
          </Link>
        </div>
      )}

      {alertOpen && (
        <AlertCreateModal
          assetId={asset.id}
          symbol={asset.symbol}
          assetName={asset.name}
          lastPrice={lastClose}
          onClose={() => setAlertOpen(false)}
          onCreated={(a) => setLastCreatedAlert(a)}
        />
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
        <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
          <div className="mb-3 flex items-center justify-between gap-3">
            <TimeframePicker selected={tfId} onPick={onPickTf} />
            <div className="flex items-center gap-3 text-[11px] text-zinc-400 dark:text-zinc-500">
              <button
                type="button"
                onClick={() => setShowForecast((v) => !v)}
                disabled={fc.status !== "ready"}
                title={
                  fc.status === "ready"
                    ? showForecast
                      ? "Hide forecast overlay"
                      : "Show forecast overlay"
                    : "Forecast not ready yet"
                }
                className={[
                  "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 transition-colors",
                  showForecast && fc.status === "ready"
                    ? "border-indigo-500/60 bg-indigo-500/10 text-indigo-700 dark:border-indigo-400/60 dark:text-indigo-300"
                    : "border-zinc-200 text-zinc-500 hover:text-zinc-800 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200",
                ].join(" ")}
              >
                {showForecast ? (
                  <EyeOff className="h-3 w-3" />
                ) : (
                  <Eye className="h-3 w-3" />
                )}
                Forecast
              </button>
              <span className="inline-flex items-center gap-1.5">
                <MousePointerClick className="h-3 w-3" />
                Click two points to measure
              </span>
            </div>
          </div>

          {visiblePoints.length === 0 ? (
            <div className="flex h-[380px] items-center justify-center text-sm text-zinc-500 dark:text-zinc-400">
              No bars in this window. Try &ldquo;All&rdquo;.
            </div>
          ) : (
            <CandleChart
              points={visiblePoints}
              dark={dark}
              forecast={showForecast ? fc.data : null}
              measure={{
                first: measure.first
                  ? {
                      time: measure.first.time as never,
                      price: measure.first.price,
                    }
                  : null,
                second: measure.second
                  ? {
                      time: measure.second.time as never,
                      price: measure.second.price,
                    }
                  : null,
                onClick: onChartClick,
              }}
            />
          )}

          <MeasureReadout
            measure={measure}
            onClear={() => setMeasure(MEASURE_EMPTY)}
          />

          <div className="mt-2 flex items-center justify-between text-[11px] text-zinc-400 dark:text-zinc-500">
            <span>
              {visiblePoints.length} bar{visiblePoints.length === 1 ? "" : "s"} ·{" "}
              {tf.title}
            </span>
            {visiblePoints.length > 0 && (
              <span>
                {visiblePoints[0].timestamp.replace("T", " ").slice(0, 16)} →{" "}
                {visiblePoints[visiblePoints.length - 1].timestamp
                  .replace("T", " ")
                  .slice(0, 16)}{" "}
                UTC
              </span>
            )}
          </div>

          <ForecastCaption
            fc={fc}
            showForecast={showForecast}
            onRetrain={onRetrain}
          />
        </div>

        <aside className="flex flex-col gap-4">
          <NewsPanel key={asset.symbol} symbol={asset.symbol} />
        </aside>
      </div>

      <PerformancePanel allPoints={series.points} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <StatsPanel points={visiblePoints} tfTitle={tf.title} last={last} />
        <AlertsForAssetPanel
          assetId={asset.id}
          symbol={asset.symbol}
          lastCreatedId={lastCreatedAlert?.id ?? null}
          onCreate={() => setAlertOpen(true)}
        />
        <LatestBarPanel last={last} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Timeframe picker
// ---------------------------------------------------------------------------

function TimeframePicker({
  selected,
  onPick,
}: {
  selected: TimeframeId;
  onPick: (id: TimeframeId) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-zinc-200 p-0.5 dark:border-zinc-800">
      {TIMEFRAMES.map((tf) => (
        <button
          key={tf.id}
          type="button"
          onClick={() => onPick(tf.id)}
          title={tf.title}
          className={[
            "rounded-sm px-2.5 py-1 text-[11px] font-semibold transition-colors",
            selected === tf.id
              ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
              : "text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800",
          ].join(" ")}
        >
          {tf.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Measure readout
// ---------------------------------------------------------------------------

function MeasureReadout({
  measure,
  onClear,
}: {
  measure: MeasureState;
  onClear: () => void;
}) {
  if (!measure.first) return null;
  const { first, second } = measure;
  if (!second) {
    return (
      <div className="mt-3 flex items-center justify-between rounded-md border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs dark:border-indigo-900 dark:bg-indigo-950/60">
        <span className="text-indigo-700 dark:text-indigo-300">
          <span className="font-mono font-semibold">A</span> set at{" "}
          {fmtPrice(first.price)}. Click a second point to measure.
        </span>
        <button
          type="button"
          onClick={onClear}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-indigo-700 hover:underline dark:text-indigo-300"
        >
          <X className="h-3 w-3" /> Clear
        </button>
      </div>
    );
  }

  const delta = second.price - first.price;
  const pct = first.price ? (delta / first.price) * 100 : null;
  const durMs = (second.time - first.time) * 1000;
  const isUp = delta >= 0;
  const toneCls = isUp
    ? "text-emerald-700 dark:text-emerald-300 border-emerald-300 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/60"
    : "text-rose-700 dark:text-rose-300 border-rose-300 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/60";

  return (
    <div
      className={`mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2 text-xs ${toneCls}`}
    >
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 font-mono tabular-nums">
        <span>
          <span className="font-semibold">A</span> {fmtPrice(first.price)}
        </span>
        <span>
          <span className="font-semibold">B</span> {fmtPrice(second.price)}
        </span>
        <span>
          Δ {fmtDelta(delta)}
          {pct !== null && <> ({fmtPct(pct)})</>}
        </span>
        <span>over {fmtDuration(durMs)}</span>
      </div>
      <button
        type="button"
        onClick={onClear}
        className="inline-flex items-center gap-1 text-[11px] font-medium hover:underline"
      >
        <X className="h-3 w-3" /> Clear
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Performance panel — % change across time windows
// ---------------------------------------------------------------------------

interface PerfBucket {
  id: string;
  label: string;
  windowMs: number;
}

const PERF_BUCKETS: PerfBucket[] = [
  { id: "15m", label: "15m", windowMs: 15 * 60 * 1000 },
  { id: "1h", label: "1h", windowMs: 60 * 60 * 1000 },
  { id: "4h", label: "4h", windowMs: 4 * 60 * 60 * 1000 },
  { id: "1d", label: "1d", windowMs: 24 * 60 * 60 * 1000 },
  { id: "3d", label: "3d", windowMs: 3 * 24 * 60 * 60 * 1000 },
  { id: "1w", label: "1w", windowMs: 7 * 24 * 60 * 60 * 1000 },
];

function PerformancePanel({ allPoints }: { allPoints: PricePoint[] }) {
  if (allPoints.length === 0) return null;
  const lastPt = allPoints[allPoints.length - 1];
  const lastPrice = Number(lastPt.close);
  const lastMs = parseBarMs(lastPt.timestamp);

  const rows = PERF_BUCKETS.map((b) => {
    const cutoff = lastMs - b.windowMs;
    const anchor = allPoints.find((p) => parseBarMs(p.timestamp) >= cutoff);
    if (!anchor) return { ...b, pct: null, available: false };
    const anchorPrice = Number(anchor.close);
    if (!anchorPrice) return { ...b, pct: null, available: true };
    return {
      ...b,
      pct: ((lastPrice - anchorPrice) / anchorPrice) * 100,
      available: true,
    };
  });
  // Also compute "All" — from the oldest bar we have.
  const oldest = Number(allPoints[0].close);
  const allPct =
    oldest && allPoints.length > 1 ? ((lastPrice - oldest) / oldest) * 100 : null;

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Performance
      </h3>
      <dl className="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-7">
        {rows.map((r) => (
          <PerfCell key={r.id} label={r.label} pct={r.pct} />
        ))}
        <PerfCell label="All" pct={allPct} />
      </dl>
    </div>
  );
}

function PerfCell({ label, pct }: { label: string; pct: number | null }) {
  const dir: "up" | "down" | "flat" =
    pct === null ? "flat" : pct > 0 ? "up" : pct < 0 ? "down" : "flat";
  return (
    <div className="rounded-md bg-zinc-50 p-3 dark:bg-zinc-900">
      <dt className="text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </dt>
      <dd
        className={`mt-0.5 text-sm font-semibold tabular-nums ${tone(dir)}`}
      >
        {pct === null ? "—" : fmtPct(pct)}
      </dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stats panel — high/low/avg vol for the selected window
// ---------------------------------------------------------------------------

function StatsPanel({
  points,
  tfTitle,
  last,
}: {
  points: PricePoint[];
  tfTitle: string;
  last: PricePoint | undefined;
}) {
  if (points.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Stats
        </h3>
        <p className="mt-3 text-sm text-zinc-500 dark:text-zinc-400">—</p>
      </div>
    );
  }
  let high = -Infinity;
  let low = Infinity;
  let volSum = 0;
  for (const p of points) {
    const h = Number(p.high);
    const l = Number(p.low);
    if (h > high) high = h;
    if (l < low) low = l;
    volSum += p.volume;
  }
  const avgVol = volSum / points.length;
  const rangePct = low ? ((high - low) / low) * 100 : null;
  const firstClose = Number(points[0].close);
  const lastClose = last ? Number(last.close) : firstClose;
  const windowChange = firstClose
    ? ((lastClose - firstClose) / firstClose) * 100
    : null;

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Stats <span className="ml-1 font-normal normal-case text-zinc-400 dark:text-zinc-500">· {tfTitle.toLowerCase()}</span>
      </h3>
      <dl className="mt-3 grid grid-cols-2 gap-y-2 text-sm">
        <Stat label="High" value={fmtPrice(high)} />
        <Stat label="Low" value={fmtPrice(low)} />
        <Stat
          label="Range"
          value={rangePct === null ? "—" : fmtPct(rangePct)}
        />
        <Stat
          label="Window Δ"
          value={windowChange === null ? "—" : fmtPct(windowChange)}
        />
        <Stat label="Avg vol/bar" value={fmtVolume(avgVol)} wide />
      </dl>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Alerts for this asset
// ---------------------------------------------------------------------------

interface AlertsPanelState {
  alerts: PriceAlert[];
  loading: boolean;
  error: string | null;
}

function AlertsForAssetPanel({
  assetId,
  symbol,
  lastCreatedId,
  onCreate,
}: {
  assetId: number;
  symbol: string;
  lastCreatedId: number | null;
  onCreate: () => void;
}) {
  const [state, setState] = useState<AlertsPanelState>({
    alerts: [],
    loading: true,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    listAlerts({ assetId, signal: controller.signal })
      .then((data) => {
        if (!cancelled) {
          setState({ alerts: data.alerts, loading: false, error: null });
        }
      })
      .catch((err: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setState({
          alerts: [],
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
    // Refetch whenever a new alert lands on this page — the id is enough to
    // trigger a fresh pull without having to thread the whole alert object.
  }, [assetId, lastCreatedId]);

  const armed = state.alerts.filter((a) => a.is_active && !a.triggered_at);
  const triggered = state.alerts.filter((a) => a.triggered_at !== null);

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          <Bell className="h-3.5 w-3.5" />
          Alerts for {symbol}
        </h3>
        <button
          type="button"
          onClick={onCreate}
          className="text-[11px] font-medium text-emerald-700 hover:underline dark:text-emerald-300"
        >
          + New
        </button>
      </div>

      {state.loading ? (
        <p className="mt-3 text-sm text-zinc-400 dark:text-zinc-500">Loading…</p>
      ) : state.error ? (
        <p className="mt-3 text-xs text-rose-600 dark:text-rose-400">
          {state.error}
        </p>
      ) : state.alerts.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-400 dark:text-zinc-500">
          No alerts on {symbol} yet.
        </p>
      ) : (
        <>
          <div className="mt-2 flex gap-3 text-[11px] text-zinc-500 dark:text-zinc-400">
            <span>
              <span className="font-mono font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                {armed.length}
              </span>{" "}
              armed
            </span>
            <span>
              <span className="font-mono font-semibold tabular-nums text-amber-600 dark:text-amber-400">
                {triggered.length}
              </span>{" "}
              triggered
            </span>
          </div>
          <ul className="mt-2 space-y-1.5">
            {state.alerts.slice(0, 5).map((a) => (
              <li
                key={a.id}
                className="flex items-center justify-between gap-2 text-xs"
              >
                <span
                  className={
                    a.triggered_at
                      ? "text-amber-700 dark:text-amber-400"
                      : a.is_active
                        ? "text-zinc-700 dark:text-zinc-300"
                        : "text-zinc-400 line-through dark:text-zinc-500"
                  }
                >
                  {a.direction === "above" ? "↑" : "↓"}{" "}
                  <span className="font-mono tabular-nums">
                    {Number(a.threshold).toLocaleString(undefined, {
                      maximumFractionDigits: 4,
                    })}
                  </span>
                </span>
                {a.note && (
                  <span className="truncate text-[11px] text-zinc-400 dark:text-zinc-500">
                    {a.note}
                  </span>
                )}
              </li>
            ))}
          </ul>
          {state.alerts.length > 5 && (
            <Link
              to="/alerts"
              className="mt-3 inline-block text-[11px] font-medium text-emerald-700 hover:underline dark:text-emerald-300"
            >
              See all {state.alerts.length} →
            </Link>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Latest bar (compact OHLCV)
// ---------------------------------------------------------------------------

function LatestBarPanel({ last }: { last: PricePoint | undefined }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Latest bar
      </h3>
      <dl className="mt-3 grid grid-cols-2 gap-y-2 text-sm">
        <Stat label="Open" value={last ? fmtPrice(Number(last.open)) : "—"} />
        <Stat label="High" value={last ? fmtPrice(Number(last.high)) : "—"} />
        <Stat label="Low" value={last ? fmtPrice(Number(last.low)) : "—"} />
        <Stat label="Close" value={last ? fmtPrice(Number(last.close)) : "—"} />
        <Stat
          label="Volume"
          value={last ? fmtVolume(last.volume) : "—"}
          wide
        />
      </dl>
    </div>
  );
}

function Stat({
  label,
  value,
  wide,
}: {
  label: string;
  value: string;
  wide?: boolean;
}) {
  return (
    <div className={wide ? "col-span-2" : undefined}>
      <dt className="text-[11px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
        {label}
      </dt>
      <dd className="mt-0.5 font-mono tabular-nums text-zinc-900 dark:text-zinc-100">
        {value}
      </dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// News panel
// ---------------------------------------------------------------------------

interface NewsPanelState {
  articles: Article[];
  loading: boolean;
  error: string | null;
}

const INITIAL_NEWS_STATE: NewsPanelState = {
  articles: [],
  loading: true,
  error: null,
};

function NewsPanel({ symbol }: { symbol: string }) {
  // `key={symbol}` at the call site resets this component on navigation,
  // so initial state is {loading: true, articles: []} on every symbol change.
  const [state, setState] = useState<NewsPanelState>(INITIAL_NEWS_STATE);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    listNews({ symbol, limit: 10, signal: controller.signal })
      .then((data) => {
        if (!cancelled) {
          setState({ articles: data.articles, loading: false, error: null });
        }
      })
      .catch((err: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setState({
          articles: [],
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [symbol]);

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Newspaper className="h-4 w-4 text-zinc-400" />
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Recent news
          </h3>
        </div>
        <Link
          to={`/news?symbol=${encodeURIComponent(symbol)}`}
          className="text-[11px] font-medium text-zinc-500 hover:text-emerald-700 dark:text-zinc-400 dark:hover:text-emerald-400"
        >
          See all →
        </Link>
      </div>
      <div className="mt-1">
        <NewsList
          articles={state.articles}
          loading={state.loading}
          error={state.error}
          emptyMessage={`No recent news for ${symbol}.`}
          hideSymbol={symbol}
          density="compact"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Forecast caption (inline under the chart card)
// ---------------------------------------------------------------------------

/**
 * Takes an ISO-8601 timestamp (may be naive; SQLite strips tz — we coerce
 * to UTC to keep "just now" accurate). Returns a coarse relative-time string.
 */
function fmtTrainedAgo(iso: string): string {
  const normalised = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const ms = Date.now() - Date.parse(normalised);
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d}d ago`;
  return `${Math.round(d / 7)}w ago`;
}

function ForecastCaption({
  fc,
  showForecast,
  onRetrain,
}: {
  fc: ForecastState;
  showForecast: boolean;
  onRetrain: () => void;
}) {
  // Loading on mount — deliberately quiet, no skeleton shift.
  if (fc.status === "loading") {
    return (
      <div className="mt-3 flex items-center gap-2 border-t border-zinc-200 pt-3 text-[11px] text-zinc-400 dark:border-zinc-800 dark:text-zinc-500">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading forecast…
      </div>
    );
  }

  if (fc.status === "error") {
    return (
      <div className="mt-3 flex items-center justify-between gap-3 border-t border-zinc-200 pt-3 text-[11px] dark:border-zinc-800">
        <span className="text-rose-600 dark:text-rose-400">
          Forecast failed to load: {fc.error}
        </span>
        <button
          type="button"
          onClick={onRetrain}
          disabled={fc.retraining}
          className="inline-flex items-center gap-1 rounded-md border border-zinc-200 bg-white px-2 py-1 font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          <RefreshCw
            className={`h-3 w-3 ${fc.retraining ? "animate-spin" : ""}`}
          />
          Retry
        </button>
      </div>
    );
  }

  if (fc.status === "not_trained") {
    return (
      <div className="mt-3 flex items-center justify-between gap-3 border-t border-zinc-200 pt-3 text-[11px] dark:border-zinc-800">
        <span className="inline-flex items-center gap-1.5 text-zinc-500 dark:text-zinc-400">
          <Sparkles className="h-3 w-3" />
          No forecast trained yet. SARIMAX fits a 14-day projection from your
          daily closes.
        </span>
        <button
          type="button"
          onClick={onRetrain}
          disabled={fc.retraining}
          className="inline-flex items-center gap-1 rounded-md border border-indigo-500/40 bg-indigo-500/10 px-2 py-1 font-semibold text-indigo-700 hover:bg-indigo-500/15 disabled:opacity-50 dark:border-indigo-400/40 dark:text-indigo-300"
        >
          {fc.retraining ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Sparkles className="h-3 w-3" />
          )}
          {fc.retraining ? "Training…" : "Train now"}
        </button>
      </div>
    );
  }

  // status === "ready"
  const data = fc.data;
  if (!data) return null;

  return (
    <div className="mt-3 flex items-center justify-between gap-3 border-t border-zinc-200 pt-3 text-[11px] dark:border-zinc-800">
      <span className="inline-flex items-center gap-1.5 text-zinc-500 dark:text-zinc-400">
        <Activity className="h-3 w-3" />
        Model: <span className="font-medium text-zinc-700 dark:text-zinc-300">{data.model}</span>
        <span className="text-zinc-300 dark:text-zinc-600">·</span>
        Trained{" "}
        <span className="font-medium text-zinc-700 dark:text-zinc-300">
          {fmtTrainedAgo(data.generated_at)}
        </span>
        <span className="text-zinc-300 dark:text-zinc-600">·</span>
        <span className="font-medium text-zinc-700 dark:text-zinc-300">
          {data.training_rows}
        </span>{" "}
        daily closes
        <span className="text-zinc-300 dark:text-zinc-600">·</span>
        <span className="font-medium text-zinc-700 dark:text-zinc-300">
          {data.horizon_days}-day
        </span>{" "}
        horizon
        {showForecast && (
          <span className="ml-2 rounded-full bg-indigo-500/10 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300">
            80% / 95% CI
          </span>
        )}
      </span>
      <div className="flex items-center gap-2">
        {fc.error && (
          <span className="text-rose-600 dark:text-rose-400">{fc.error}</span>
        )}
        <button
          type="button"
          onClick={onRetrain}
          disabled={fc.retraining}
          className="inline-flex items-center gap-1 rounded-md border border-zinc-200 bg-white px-2 py-1 font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          <RefreshCw
            className={`h-3 w-3 ${fc.retraining ? "animate-spin" : ""}`}
          />
          {fc.retraining ? "Retraining…" : "Retrain"}
        </button>
      </div>
    </div>
  );
}
