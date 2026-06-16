import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Activity,
  ArrowDownRight,
  ArrowLeft,
  ArrowUpRight,
  Bell,
  Briefcase,
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
  type MacroIndicator,
  type PriceAlert,
  type PricePoint,
  type PriceSeries,
  type Quote,
  type SentimentTimeseriesPoint,
  getForecast,
  getMacroSeries,
  getPriceSeries,
  getQuote,
  getSentimentTimeseries,
  listAlerts,
  listAssets,
  listMacroIndicators,
  listNews,
  retrainForecast,
} from "../api/client";
import { AlertCreateModal } from "../components/AlertCreateModal";
import { CandleChart } from "../components/CandleChart";
import { bollingerBands, regressionChannel } from "../components/indicators";
import { ForecastAccuracyPanel } from "../components/ForecastAccuracyPanel";
import { NewsList } from "../components/NewsList";
import { SentimentSummaryPanel } from "../components/SentimentSummaryPanel";
import { StrongSentimentDaysPanel } from "../components/StrongSentimentDaysPanel";
import { TransactionAddModal } from "../components/TransactionAddModal";
import { VolatilityPanel } from "../components/VolatilityPanel";
import { useResolvedTheme } from "../stores/useSettings";

interface State {
  asset: Asset | null;
  /** Intraday 5-minute series (powers the 1D timeframe). */
  series: PriceSeries | null;
  /** Daily-close series (powers 1W/1M/3M/6M/1Y/2Y/5Y/All — intraday bars only
   *  ever span ~1 day, so the multi-day views need daily resolution). */
  dailySeries: PriceSeries | null;
  quote: Quote | null;
  loading: boolean;
  error: string | null;
  notFound: boolean;
}

const INITIAL: State = {
  asset: null,
  series: null,
  dailySeries: null,
  quote: null,
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
        const [assets, series, dailySeries, quote] = await Promise.all([
          listAssets({ activeOnly: false, signal: controller.signal }),
          getPriceSeries(symbol, { limit: MAX_BARS, signal: controller.signal }),
          // Daily closes for the 1W-and-longer timeframes. Tolerate absence (a
          // brand-new asset has no daily bars until the daily job runs).
          getPriceSeries(symbol, {
            interval: "1d",
            limit: MAX_BARS,
            signal: controller.signal,
          }).catch(() => null),
          // Day change comes from the server quote; tolerate its absence
          // (e.g. no daily bars yet) so the page still renders.
          getQuote(symbol, controller.signal).catch(() => null),
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
          dailySeries,
          quote,
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
          dailySeries={state.dailySeries}
          quote={state.quote}
          dark={resolved === "dark"}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Timeframes
// ---------------------------------------------------------------------------

type TimeframeId =
  | "1D"
  | "1W"
  | "1M"
  | "3M"
  | "6M"
  | "1Y"
  | "2Y"
  | "5Y"
  | "ALL";

/** Only "1D" reads the intraday 5-minute series (≈ today's session). Every
 *  other timeframe reads the 5-year daily-close series, where the
 *  forecast/sentiment/channel overlays line up with one candle per day. */
function isMultiDayTimeframe(id: TimeframeId): boolean {
  return id !== "1D";
}

interface Timeframe {
  id: TimeframeId;
  label: string;
  /** Window length in ms, or null for "all". */
  windowMs: number | null;
  title: string;
}

const _DAY = 24 * 60 * 60 * 1000;
const TIMEFRAMES: Timeframe[] = [
  { id: "1D", label: "1D", windowMs: _DAY, title: "Today's session (intraday)" },
  { id: "1W", label: "1W", windowMs: 7 * _DAY, title: "Last week" },
  { id: "1M", label: "1M", windowMs: 30 * _DAY, title: "Last month" },
  { id: "3M", label: "3M", windowMs: 90 * _DAY, title: "Last 3 months" },
  { id: "6M", label: "6M", windowMs: 182 * _DAY, title: "Last 6 months" },
  { id: "1Y", label: "1Y", windowMs: 365 * _DAY, title: "Last year" },
  { id: "2Y", label: "2Y", windowMs: 730 * _DAY, title: "Last 2 years" },
  { id: "5Y", label: "5Y", windowMs: 1825 * _DAY, title: "Last 5 years" },
  { id: "ALL", label: "All", windowMs: null, title: "All available history" },
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
  dailySeries,
  quote,
  dark,
}: {
  asset: Asset;
  series: PriceSeries;
  dailySeries: PriceSeries | null;
  quote: Quote | null;
  dark: boolean;
}) {
  const [alertOpen, setAlertOpen] = useState(false);
  const [lastCreatedAlert, setLastCreatedAlert] = useState<PriceAlert | null>(
    null,
  );
  const [txnOpen, setTxnOpen] = useState(false);
  const [txnFlash, setTxnFlash] = useState(false);
  const [tfId, setTfId] = useState<TimeframeId>("3M");
  const [measure, setMeasure] = useState<MeasureState>(MEASURE_EMPTY);
  const [fc, setFc] = useState<ForecastState>(FORECAST_INITIAL);
  const [showForecast, setShowForecast] = useState(false);
  // Descriptive TA overlays on the daily chart (off by default). Gated to
  // multi-day timeframes like the forecast/sentiment overlays.
  const [showRegression, setShowRegression] = useState(false);
  const [showBollinger, setShowBollinger] = useState(false);
  // Bumps after each successful retrain so the accuracy panel re-fetches.
  // We don't use `key` because re-mounting would lose the panel's last-
  // good numbers while the new fetch lands.
  const [fcRetrainTick, setFcRetrainTick] = useState(0);
  // Sentiment timeseries → daily markers on the candle chart. Only
  // surfaced for multi-day timeframes (intraday density makes the
  // markers cluster at midnight and look out of place). We fetch once
  // per asset and let the timeframe gate handle visibility.
  const [showSentimentMarkers, setShowSentimentMarkers] = useState(true);
  const [sentimentSeries, setSentimentSeries] = useState<
    SentimentTimeseriesPoint[]
  >([]);
  // Macro overlay — user picks a FRED indicator from the chart header
  // dropdown and we fetch + render it as a secondary-axis line. ``null``
  // hides the overlay; the same null state hides the left price scale.
  const [macroIndicators, setMacroIndicators] = useState<MacroIndicator[]>([]);
  const [macroSeriesId, setMacroSeriesId] = useState<string | null>(null);
  const [macroPoints, setMacroPoints] = useState<
    { date: string; value: number }[] | null
  >(null);

  // Fetch the persisted forecast on mount. AssetBody is keyed on
  // ``asset.symbol`` by the parent, so this effect only fires once per mount.
  // 404 → ``not_trained`` (shows a "Train now" CTA).
  //
  // Self-heal: if the stored forecast is *behind* the latest daily bar (its
  // training data is older than the data we now have), retrain it immediately
  // so the user never sees a projection anchored in the past. This covers the
  // race where the page loads before the launch-time bulk retrain finishes —
  // without it, an asset opened during startup would show a stale forecast
  // until manually refreshed. When the forecast is already current this is a
  // no-op (no fit, no extra request).
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    const latestDailyDate = dailySeries?.points.at(-1)?.timestamp.slice(0, 10);

    void (async () => {
      try {
        const data = await getForecast(asset.symbol, controller.signal);
        if (cancelled) return;
        const stale =
          !!latestDailyDate &&
          data.last_close_date.slice(0, 10) < latestDailyDate;
        if (!stale) {
          setFc({ data, status: "ready", error: null, retraining: false });
          return;
        }
        // Show the stale forecast immediately, flagged retraining, then swap
        // in the fresh one when the fit returns.
        setFc({ data, status: "ready", error: null, retraining: true });
        try {
          const fresh = await retrainForecast(asset.symbol);
          if (!cancelled)
            setFc({ data: fresh, status: "ready", error: null, retraining: false });
        } catch {
          // Retrain failed (e.g. transient) — keep showing the stale forecast
          // rather than blanking the panel.
          if (!cancelled)
            setFc({ data, status: "ready", error: null, retraining: false });
        }
      } catch (err: unknown) {
        if (cancelled || controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) {
          setFc({ data: null, status: "not_trained", error: null, retraining: false });
          return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        setFc({ data: null, status: "error", error: msg, retraining: false });
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [asset.symbol, dailySeries]);

  // Pull the daily sentiment timeseries once per asset. Used by the
  // candle chart to flag strong-news days as colored markers. Errors
  // are non-fatal — markers just stay hidden.
  useEffect(() => {
    const controller = new AbortController();
    getSentimentTimeseries(asset.symbol, {
      days: 90,
      signal: controller.signal,
    })
      .then((data) => setSentimentSeries(data.points))
      .catch(() => {
        if (controller.signal.aborted) return;
        setSentimentSeries([]);
      });
    return () => controller.abort();
  }, [asset.symbol]);

  // Macro indicator catalog — populates the chart-header dropdown.
  // Asset-independent, so we fetch once on mount and never refresh.
  useEffect(() => {
    const controller = new AbortController();
    listMacroIndicators({ activeOnly: true, signal: controller.signal })
      .then(setMacroIndicators)
      .catch(() => {
        if (controller.signal.aborted) return;
        setMacroIndicators([]);
      });
    return () => controller.abort();
  }, []);

  // Fetch the chosen macro series whenever the user picks a different
  // one. Coerces the Decimal-as-string ``value`` to a float so the chart
  // can plot it directly. Limit chosen large enough to cover macro
  // series like DGS10 (daily, ~16k rows since 1962) — lightweight-charts
  // handles tens of thousands of points without breaking a sweat.
  //
  // The "user picked None" path lives in the dropdown's onChange handler
  // — calling setMacroPoints(null) from the effect body trips the React
  // 19 ``react-hooks/set-state-in-effect`` rule.
  useEffect(() => {
    if (!macroSeriesId) return;
    const controller = new AbortController();
    getMacroSeries(macroSeriesId, { limit: 5000, signal: controller.signal })
      .then((series) =>
        setMacroPoints(
          series.points.map((p) => ({ date: p.date, value: Number(p.value) })),
        ),
      )
      .catch(() => {
        if (controller.signal.aborted) return;
        setMacroPoints([]);
      });
    return () => controller.abort();
  }, [macroSeriesId]);

  const onRetrain = async () => {
    setFc((s) => ({ ...s, retraining: true, error: null }));
    try {
      const data = await retrainForecast(asset.symbol);
      setFc({ data, status: "ready", error: null, retraining: false });
      // If the user hit "Train now" from a cold state, show them the result
      // immediately — no point hiding what they just asked for.
      setShowForecast(true);
      // Tell the accuracy panel to refresh — every retrain appends a new
      // snapshot the metrics should pick up.
      setFcRetrainTick((t) => t + 1);
    } catch (err) {
      let msg = err instanceof Error ? err.message : String(err);
      if (err instanceof ApiError) {
        if (err.status === 422) {
          msg =
            "Not enough daily history yet. The daily-bar job needs " +
            "to run for a while before the forecaster can fit.";
        }
      }
      setFc((s) => ({ ...s, retraining: false, error: msg }));
    }
  };

  const tf = TIMEFRAMES.find((t) => t.id === tfId) ?? TIMEFRAMES[TIMEFRAMES.length - 1];
  // Intraday (5m) bars only ever span ~1 day, so anything beyond 1D would slice
  // to the same single session and look identical. The 1W-and-longer timeframes
  // therefore read the daily-close series instead; only 1D keeps the 5m series.
  const visiblePoints = useMemo(() => {
    const src = isMultiDayTimeframe(tf.id)
      ? (dailySeries?.points ?? [])
      : series.points;
    return sliceToTimeframe(src, tf);
  }, [series.points, dailySeries, tf]);

  // Descriptive TA overlays, computed from the visible daily closes. Only on
  // multi-day timeframes (they're daily-resolution indicators).
  const overlaysEnabled = isMultiDayTimeframe(tf.id);
  const regressionData = useMemo(
    () => (overlaysEnabled && showRegression ? regressionChannel(visiblePoints) : null),
    [overlaysEnabled, showRegression, visiblePoints],
  );
  const bollingerData = useMemo(
    () => (overlaysEnabled && showBollinger ? bollingerBands(visiblePoints) : null),
    [overlaysEnabled, showBollinger, visiblePoints],
  );

  const last = visiblePoints[visiblePoints.length - 1];
  // Last price + day change come from the server quote (previous-session close
  // vs. live price). Fall back to the latest visible bar's close for price only
  // when no quote is available; the header "change" is a true *day* change, not
  // the last-two-bar delta the timeframe toggle might imply.
  const lastClose =
    quote?.last_price != null
      ? Number(quote.last_price)
      : last
        ? Number(last.close)
        : null;
  const changePct = quote?.change_pct ?? null;
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
            onClick={() => setTxnOpen(true)}
            className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-indigo-500/40 bg-indigo-500/5 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-500/10 dark:border-indigo-500/40 dark:text-indigo-300"
          >
            <Briefcase className="h-3.5 w-3.5" />
            Record transaction
          </button>
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

      {txnOpen && (
        <TransactionAddModal
          assets={[asset]}
          defaultAssetId={asset.id}
          onClose={() => setTxnOpen(false)}
          onCreated={() => {
            setTxnOpen(false);
            setTxnFlash(true);
            setTimeout(() => setTxnFlash(false), 6000);
          }}
        />
      )}

      {txnFlash && (
        <div className="rounded-md border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs text-indigo-800 dark:border-indigo-900 dark:bg-indigo-950 dark:text-indigo-300">
          Transaction recorded for {asset.symbol}.{" "}
          <Link to="/portfolio" className="font-medium underline">
            View portfolio →
          </Link>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
        <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-y-2 gap-x-3">
            <TimeframePicker selected={tfId} onPick={onPickTf} />
            <div className="flex items-center gap-3 text-[11px] text-zinc-400 dark:text-zinc-500">
              <button
                type="button"
                onClick={() => setShowForecast((v) => !v)}
                disabled={fc.status !== "ready" || !isMultiDayTimeframe(tfId)}
                title={
                  !isMultiDayTimeframe(tfId)
                    ? "Forecast is a 14-day daily projection — shown on daily timeframes (1W and longer)"
                    : fc.status === "ready"
                      ? showForecast
                        ? "Hide forecast overlay"
                        : "Show forecast overlay"
                      : "Forecast not ready yet"
                }
                className={[
                  "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 transition-colors",
                  showForecast && fc.status === "ready" && isMultiDayTimeframe(tfId)
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
              <button
                type="button"
                onClick={() => setShowSentimentMarkers((v) => !v)}
                disabled={
                  !isMultiDayTimeframe(tfId) || sentimentSeries.length === 0
                }
                title={
                  !isMultiDayTimeframe(tfId)
                    ? "Sentiment markers are only shown on daily+ timeframes"
                    : sentimentSeries.length === 0
                      ? "No sentiment data for this asset yet"
                      : showSentimentMarkers
                        ? "Hide sentiment markers"
                        : "Show sentiment markers"
                }
                className={[
                  "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 transition-colors",
                  showSentimentMarkers &&
                  isMultiDayTimeframe(tfId) &&
                  sentimentSeries.length > 0
                    ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-700 dark:border-emerald-400/60 dark:text-emerald-300"
                    : "border-zinc-200 text-zinc-500 hover:text-zinc-800 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200",
                ].join(" ")}
              >
                {showSentimentMarkers ? (
                  <EyeOff className="h-3 w-3" />
                ) : (
                  <Eye className="h-3 w-3" />
                )}
                Sentiment
              </button>
              <button
                type="button"
                onClick={() => setShowRegression((v) => !v)}
                disabled={!isMultiDayTimeframe(tfId)}
                title={
                  !isMultiDayTimeframe(tfId)
                    ? "Trend channel is a daily indicator — shown on daily timeframes (1W and longer)"
                    : showRegression
                      ? "Hide regression channel"
                      : "Show regression channel (descriptive trend ± rails)"
                }
                className={[
                  "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 transition-colors",
                  showRegression && isMultiDayTimeframe(tfId)
                    ? "border-teal-500/60 bg-teal-500/10 text-teal-700 dark:border-teal-400/60 dark:text-teal-300"
                    : "border-zinc-200 text-zinc-500 hover:text-zinc-800 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200",
                ].join(" ")}
              >
                Channel
              </button>
              <button
                type="button"
                onClick={() => setShowBollinger((v) => !v)}
                disabled={!isMultiDayTimeframe(tfId)}
                title={
                  !isMultiDayTimeframe(tfId)
                    ? "Bollinger bands are a daily indicator — shown on daily timeframes (1W and longer)"
                    : showBollinger
                      ? "Hide Bollinger bands"
                      : "Show Bollinger bands (SMA ± 2σ)"
                }
                className={[
                  "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 transition-colors",
                  showBollinger && isMultiDayTimeframe(tfId)
                    ? "border-orange-500/60 bg-orange-500/10 text-orange-700 dark:border-orange-400/60 dark:text-orange-300"
                    : "border-zinc-200 text-zinc-500 hover:text-zinc-800 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200",
                ].join(" ")}
              >
                Bollinger
              </button>
              <label className="inline-flex items-center gap-1.5">
                <span>Macro:</span>
                <select
                  value={macroSeriesId ?? ""}
                  onChange={(e) => {
                    const next = e.target.value === "" ? null : e.target.value;
                    setMacroSeriesId(next);
                    // Clear stale points eagerly so the chart doesn't
                    // briefly show the previous overlay while the new
                    // series fetches (or while transitioning to None).
                    if (next === null) setMacroPoints(null);
                  }}
                  disabled={macroIndicators.length === 0}
                  className="rounded-sm border border-zinc-200 bg-white px-1 py-0.5 text-[11px] font-medium text-zinc-700 disabled:opacity-40 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
                >
                  <option value="">None</option>
                  {macroIndicators.map((m) => (
                    <option key={m.series_id} value={m.series_id}>
                      {m.series_id}
                    </option>
                  ))}
                </select>
              </label>
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
              forecast={
                showForecast && isMultiDayTimeframe(tfId) ? fc.data : null
              }
              sentiment={
                showSentimentMarkers && isMultiDayTimeframe(tfId)
                  ? sentimentSeries
                  : null
              }
              regressionChannel={regressionData}
              bollinger={bollingerData}
              macroOverlay={
                macroSeriesId && macroPoints
                  ? {
                      points: macroPoints,
                      label:
                        macroIndicators.find(
                          (m) => m.series_id === macroSeriesId,
                        )?.name ?? macroSeriesId,
                    }
                  : null
              }
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
          <SentimentSummaryPanel key={`sentiment-${asset.symbol}`} symbol={asset.symbol} />
          <NewsPanel key={asset.symbol} symbol={asset.symbol} />
        </aside>
      </div>

      <PerformancePanel
        intradayPoints={series.points}
        dailyPoints={dailySeries?.points ?? []}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ForecastAccuracyPanel
          symbol={asset.symbol}
          refreshTick={fcRetrainTick}
        />
        <VolatilityPanel symbol={asset.symbol} />
      </div>

      <StrongSentimentDaysPanel
        symbol={asset.symbol}
        sentimentSeries={sentimentSeries}
      />


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
  { id: "1d", label: "1D", windowMs: 1 * _DAY },
  { id: "1w", label: "1W", windowMs: 7 * _DAY },
  { id: "1m", label: "1M", windowMs: 30 * _DAY },
  { id: "3m", label: "3M", windowMs: 90 * _DAY },
  { id: "6m", label: "6M", windowMs: 182 * _DAY },
  { id: "1y", label: "1Y", windowMs: 365 * _DAY },
];

const PERF_ONE_DAY_MS = 24 * 60 * 60 * 1000;

/** % change over `windowMs`, anchored to the source's own last bar (so a stale
 *  daily series still yields a valid "last N days" number). windowMs=Infinity
 *  anchors to the oldest bar ("All"). */
function pctOverWindow(points: PricePoint[], windowMs: number): number | null {
  if (points.length < 2) return null;
  const last = points[points.length - 1];
  const lastPrice = Number(last.close);
  const cutoff = parseBarMs(last.timestamp) - windowMs;
  const anchor = points.find((p) => parseBarMs(p.timestamp) >= cutoff);
  if (!anchor) return null;
  const anchorPrice = Number(anchor.close);
  return anchorPrice ? ((lastPrice - anchorPrice) / anchorPrice) * 100 : null;
}

function PerformancePanel({
  intradayPoints,
  dailyPoints,
}: {
  intradayPoints: PricePoint[];
  dailyPoints: PricePoint[];
}) {
  if (intradayPoints.length === 0 && dailyPoints.length === 0) return null;

  const rows = PERF_BUCKETS.map((b) => {
    // Windows longer than a day read the daily series — intraday bars only
    // span ~1 session, so a "1w" change computed from 5m data is meaningless.
    const source = b.windowMs > PERF_ONE_DAY_MS ? dailyPoints : intradayPoints;
    return { ...b, pct: pctOverWindow(source, b.windowMs) };
  });
  // "All" — full daily history.
  const allPct = pctOverWindow(dailyPoints, Number.POSITIVE_INFINITY);

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
          No forecast trained yet. The default engine fits a 14-day projection
          from your daily closes (change it in Settings → ML).
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
