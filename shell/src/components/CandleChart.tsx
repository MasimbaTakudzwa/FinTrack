import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { barTimeSeconds } from "../api/client";
import type {
  Forecast,
  PricePoint,
  SentimentTimeseriesPoint,
} from "../api/client";
import { ForecastFan, type FanPoint } from "./forecastFan";
import type { ChannelData } from "./indicators";

interface MeasurePoint {
  time: UTCTimestamp;
  price: number;
}

interface Props {
  points: PricePoint[];
  dark: boolean;
  height?: number;
  /**
   * Click-to-measure state. When present, the chart draws markers at the
   * selected points. Parent owns the state so it can also render the readout.
   */
  measure?: {
    first: MeasurePoint | null;
    second: MeasurePoint | null;
    /**
     * Click handler — chart resolves the click coordinates to
     * (time, price) and calls this. Parent decides what to do with it.
     */
    onClick: (p: MeasurePoint) => void;
  };
  /**
   * SARIMAX projection overlay. When set, the chart draws a dashed indigo
   * median line plus four bounding lines at the 80% and 95% CI edges,
   * starting from the day after ``last_close_date``. Pass null / undefined
   * to hide.
   */
  forecast?: Forecast | null;
  /**
   * Daily sentiment timeseries. When set, days whose mean compound score
   * crosses the strong-signal threshold are rendered as colored markers
   * above the candle for that day — emerald for positive, rose for
   * negative. Pass `null` / `undefined` to hide. Markers don't fight
   * with measure markers; both share one markers plugin which is
   * rebuilt from both sources whenever either changes.
   */
  sentiment?: SentimentTimeseriesPoint[] | null;
  /**
   * Macro indicator overlay (FRED series). When set, renders the series
   * as a thin amber line on a separate price scale on the LEFT side of
   * the chart so the candles' axis on the right stays uncluttered.
   * Useful for "did Fed cuts move SPY?" type comparisons. ``label``
   * is shown in the chart legend / tooltip.
   */
  macroOverlay?: { points: { date: string; value: number }[]; label: string } | null;
  /** Descriptive linear-regression channel (mid + ±k·σ rails). Null hides it. */
  regressionChannel?: ChannelData | null;
  /** Descriptive Bollinger bands (SMA ± 2σ). Null hides it. */
  bollinger?: ChannelData | null;
}

/** Minimum |mean compound| to surface a day as a chart marker. Matches
 *  the bucket-classification thresholds in `ml.sentiment` (positive /
 *  negative cutoffs at ±0.05) but raised to 0.30 here so we only flag
 *  days with genuinely strong news polarity, not slightly-leaning ones. */
const SENTIMENT_MARKER_THRESHOLD = 0.3;
/** Minimum scored-headline count for a day to qualify — single-headline
 *  days are too noisy to surface as a marker. */
const SENTIMENT_MARKER_MIN_COUNT = 2;

function toCandles(points: PricePoint[]): CandlestickData<UTCTimestamp>[] {
  return points.map((p) => ({
    // barTimeSeconds appends `Z` to the offset-less backend timestamp so the
    // axis is UTC, not browser-local (which shifted every candle by the user's
    // offset and broke the measurement tool's time basis).
    time: barTimeSeconds(p.timestamp) as UTCTimestamp,
    open: Number(p.open),
    high: Number(p.high),
    low: Number(p.low),
    close: Number(p.close),
  }));
}

function toVolume(points: PricePoint[]): HistogramData<UTCTimestamp>[] {
  return points.map((p) => {
    const up = Number(p.close) >= Number(p.open);
    return {
      time: barTimeSeconds(p.timestamp) as UTCTimestamp,
      value: p.volume,
      color: up ? "rgba(16, 185, 129, 0.4)" : "rgba(244, 63, 94, 0.4)",
    };
  });
}

function palette(dark: boolean) {
  return dark
    ? {
        background: "transparent",
        text: "#d4d4d8",
        grid: "#27272a",
        border: "#3f3f46",
        up: "#10b981",
        down: "#f43f5e",
        marker: "#6366f1",
        forecastMedian: "#a5b4fc", // indigo-300
        forecastBand80: "rgba(165, 180, 252, 0.70)",
        forecastBand95: "rgba(165, 180, 252, 0.35)",
        sentimentPositive: "#34d399", // emerald-400
        sentimentNegative: "#fb7185", // rose-400
        macro: "#fbbf24", // amber-400
        regression: "#2dd4bf", // teal-400
        bollinger: "#fb923c", // orange-400
      }
    : {
        background: "transparent",
        text: "#3f3f46",
        grid: "#e4e4e7",
        border: "#d4d4d8",
        up: "#059669",
        down: "#e11d48",
        marker: "#4f46e5",
        forecastMedian: "#4f46e5", // indigo-600
        forecastBand80: "rgba(79, 70, 229, 0.55)",
        forecastBand95: "rgba(79, 70, 229, 0.30)",
        sentimentPositive: "#10b981", // emerald-500
        sentimentNegative: "#f43f5e", // rose-500
        macro: "#d97706", // amber-600
        regression: "#0d9488", // teal-600
        bollinger: "#ea580c", // orange-600
      };
}

function toMacroLine(
  points: { date: string; value: number }[],
): LineData<UTCTimestamp>[] {
  return points.map((p) => ({
    time: (Date.parse(`${p.date}T00:00:00Z`) / 1000) as UTCTimestamp,
    value: p.value,
  }));
}

/**
 * Forecast points are dated (``"YYYY-MM-DD"``). Convert to a lightweight-charts
 * ``UTCTimestamp`` anchored at midnight UTC so they line up cleanly with the
 * most recent candle on both daily and intraday axes.
 */
function toLine(
  forecast: Forecast,
  accessor: (p: Forecast["points"][number]) => number,
): LineData<UTCTimestamp>[] {
  return forecast.points.map((p) => ({
    time: (Date.parse(`${p.forecast_date}T00:00:00Z`) / 1000) as UTCTimestamp,
    value: accessor(p),
  }));
}

/**
 * Forecast overlay handle. The dashed median stays a LineSeries; the 80%/95%
 * tolerance bands are filled as a shaded cone by the ForecastFan primitive
 * (see forecastFan.ts) — lightweight-charts v5 has no native area-between-
 * curves series, so the fan paints the polygons directly on the pane canvas.
 */
interface ForecastSeries {
  median: ISeriesApi<"Line">;
}

/** Three line-series handles for a descriptive channel overlay (mid + rails). */
interface ChannelSeries {
  mid: ISeriesApi<"Line">;
  upper: ISeriesApi<"Line">;
  lower: ISeriesApi<"Line">;
}

function channelToLineData(pts: { time: number; value: number }[]): LineData<UTCTimestamp>[] {
  return pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }));
}

export function CandleChart({
  points,
  dark,
  height = 380,
  measure,
  forecast,
  sentiment,
  macroOverlay,
  regressionChannel,
  bollinger,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const forecastRef = useRef<ForecastSeries | null>(null);
  const fanRef = useRef<ForecastFan | null>(null);
  const regRef = useRef<ChannelSeries | null>(null);
  const bollRef = useRef<ChannelSeries | null>(null);
  const macroRef = useRef<ISeriesApi<"Line"> | null>(null);
  // Keep the latest onClick inside a ref so the subscribe effect doesn't
  // resubscribe on every render (subscribing + unsubscribing re-triggers
  // the chart's event listener reseat).
  const onClickRef = useRef<((p: MeasurePoint) => void) | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const p = palette(dark);

    const chart = createChart(containerRef.current, {
      height,
      autoSize: true,
      layout: {
        background: { color: p.background },
        textColor: p.text,
      },
      grid: {
        vertLines: { color: p.grid },
        horzLines: { color: p.grid },
      },
      rightPriceScale: { borderColor: p.border },
      timeScale: {
        borderColor: p.border,
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: 1 },
    });

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: p.up,
      downColor: p.down,
      wickUpColor: p.up,
      wickDownColor: p.down,
      borderVisible: false,
    });

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volume.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const markers = createSeriesMarkers(candle, []);

    // Forecast overlay series — created eagerly so we can push data in the
    // `forecast`-effect below without touching the chart lifecycle. Each
    // series has `lastValueVisible=false` to avoid polluting the right-hand
    // price scale with five duplicate labels, and `priceLineVisible=false`
    // to suppress the horizontal crosshair guide that `LineSeries` adds by
    // default. Same chart / same priceScaleId ("right") so they respect the
    // price axis alongside the candles.
    const commonLineOpts = {
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    } as const;
    const forecastMedian = chart.addSeries(LineSeries, {
      ...commonLineOpts,
      color: p.forecastMedian,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
    });
    // Shaded tolerance cone behind the median — drawn by a pane primitive
    // attached to the candle series (filled 95% + nested 80% bands). Low-alpha
    // indigo fills so the candles + median stay legible through the cone.
    const fan = new ForecastFan({
      band95: dark ? "rgba(165, 180, 252, 0.12)" : "rgba(99, 102, 241, 0.10)",
      band80: dark ? "rgba(165, 180, 252, 0.26)" : "rgba(99, 102, 241, 0.22)",
    });
    candle.attachPrimitive(fan);

    // Macro overlay series — separate price scale on the LEFT side of
    // the chart so a unit-mismatched indicator (FedFunds in % vs price
    // in $) doesn't squash the candles. Visible only when macroOverlay
    // data is set; its scale auto-fits when populated.
    const macro = chart.addSeries(LineSeries, {
      color: p.macro,
      lineWidth: 2,
      lastValueVisible: true,
      priceLineVisible: false,
      crosshairMarkerVisible: true,
      priceScaleId: "left",
    });
    macro.priceScale().applyOptions({
      visible: false, // keep the axis itself hidden until data lands
      scaleMargins: { top: 0.1, bottom: 0.4 },
    });

    // Descriptive overlays (regression channel = teal, Bollinger = amber) on
    // the right price scale alongside the candles. Mid solid, rails dashed.
    const mkChannel = (color: string): ChannelSeries => ({
      mid: chart.addSeries(LineSeries, { ...commonLineOpts, color, lineWidth: 2 }),
      upper: chart.addSeries(LineSeries, {
        ...commonLineOpts,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
      }),
      lower: chart.addSeries(LineSeries, {
        ...commonLineOpts,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
      }),
    });
    const reg = mkChannel(p.regression);
    const boll = mkChannel(p.bollinger);

    chartRef.current = chart;
    candleRef.current = candle;
    volumeRef.current = volume;
    markersRef.current = markers;
    forecastRef.current = { median: forecastMedian };
    fanRef.current = fan;
    regRef.current = reg;
    bollRef.current = boll;
    macroRef.current = macro;

    const handleClick = (param: MouseEventParams) => {
      const cb = onClickRef.current;
      if (!cb) return;
      if (param.time === undefined || !param.point) return;
      const price = candle.coordinateToPrice(param.point.y);
      if (price === null) return;
      cb({ time: param.time as UTCTimestamp, price });
    };
    chart.subscribeClick(handleClick);

    return () => {
      chart.unsubscribeClick(handleClick);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      markersRef.current = null;
      forecastRef.current = null;
      fanRef.current = null;
      regRef.current = null;
      bollRef.current = null;
      macroRef.current = null;
    };
  }, [dark, height]);

  // Push descriptive overlay data (regression channel / Bollinger bands) into
  // their series; empty arrays hide them.
  useEffect(() => {
    const reg = regRef.current;
    if (!reg) return;
    reg.mid.setData(regressionChannel ? channelToLineData(regressionChannel.mid) : []);
    reg.upper.setData(regressionChannel ? channelToLineData(regressionChannel.upper) : []);
    reg.lower.setData(regressionChannel ? channelToLineData(regressionChannel.lower) : []);
  }, [regressionChannel]);

  useEffect(() => {
    const boll = bollRef.current;
    if (!boll) return;
    boll.mid.setData(bollinger ? channelToLineData(bollinger.mid) : []);
    boll.upper.setData(bollinger ? channelToLineData(bollinger.upper) : []);
    boll.lower.setData(bollinger ? channelToLineData(bollinger.lower) : []);
  }, [bollinger]);

  useEffect(() => {
    if (!candleRef.current || !volumeRef.current) return;
    candleRef.current.setData(toCandles(points));
    volumeRef.current.setData(toVolume(points));
    chartRef.current?.timeScale().fitContent();
  }, [points]);

  // Keep the click callback ref fresh without re-creating the chart.
  useEffect(() => {
    onClickRef.current = measure?.onClick ?? null;
  }, [measure?.onClick]);

  // Reflect measure-point markers + sentiment markers on the chart in a
  // single setMarkers call. Both sources share the one markers plugin
  // attached to the candle series; if we managed them in separate
  // effects the second setMarkers would clobber the first's output.
  useEffect(() => {
    if (!markersRef.current) return;
    const p = palette(dark);
    const merged: SeriesMarker<Time>[] = [];

    // Measure points first so they sort before sentiment markers when
    // they happen to fall on the same date.
    const measurePts: MeasurePoint[] = [];
    if (measure?.first) measurePts.push(measure.first);
    if (measure?.second) measurePts.push(measure.second);
    measurePts.forEach((pt, idx) => {
      merged.push({
        time: pt.time,
        position: "inBar",
        shape: "circle",
        color: p.marker,
        text: idx === 0 ? "A" : "B",
      });
    });

    if (sentiment && sentiment.length > 0) {
      for (const s of sentiment) {
        if (
          Math.abs(s.mean) < SENTIMENT_MARKER_THRESHOLD ||
          s.count < SENTIMENT_MARKER_MIN_COUNT
        ) {
          continue;
        }
        const isPositive = s.mean > 0;
        // Anchor at midnight UTC of the bucket date — matches how the
        // sentiment-timeline chart positions its histogram bars.
        const time = (Date.parse(`${s.date}T00:00:00Z`) / 1000) as UTCTimestamp;
        merged.push({
          time,
          position: isPositive ? "aboveBar" : "belowBar",
          shape: isPositive ? "arrowUp" : "arrowDown",
          color: isPositive ? p.sentimentPositive : p.sentimentNegative,
          text: `${s.count}`,
        });
      }
    }

    // The plugin requires markers in ascending time order to render
    // correctly — sort by epoch seconds.
    merged.sort((a, b) => Number(a.time) - Number(b.time));
    markersRef.current.setMarkers(merged);
  }, [measure?.first, measure?.second, sentiment, dark]);

  // Push forecast data (or empty arrays to hide) into the overlay series.
  // Guarded by the chart-created ref so hot-reload re-renders don't crash on
  // a transiently-null handle.
  useEffect(() => {
    const f = forecastRef.current;
    const fan = fanRef.current;
    if (!f || !fan) return;
    if (!forecast) {
      f.median.setData([]);
      fan.setData([]);
      return;
    }
    f.median.setData(toLine(forecast, (p) => p.yhat));
    // Anchor the cone at the last actual close (zero-width) so it visibly
    // emanates from the price, then fans out over the horizon.
    const anchorTime = barTimeSeconds(`${forecast.last_close_date}T00:00:00Z`) as UTCTimestamp;
    const anchorPrice = Number(forecast.last_close);
    const fanPoints: FanPoint[] = [
      {
        time: anchorTime,
        lower95: anchorPrice,
        upper95: anchorPrice,
        lower80: anchorPrice,
        upper80: anchorPrice,
      },
      ...forecast.points.map((p) => ({
        time: barTimeSeconds(`${p.forecast_date}T00:00:00Z`) as UTCTimestamp,
        lower95: p.lower_95,
        upper95: p.upper_95,
        lower80: p.lower_80,
        upper80: p.upper_80,
      })),
    ];
    fan.setData(fanPoints);
    chartRef.current?.timeScale().fitContent();
  }, [forecast]);

  // Macro overlay series — toggle the left price scale's visibility
  // alongside the data so an empty overlay doesn't leave an empty axis
  // sitting on the chart edge.
  useEffect(() => {
    const m = macroRef.current;
    if (!m) return;
    if (!macroOverlay || macroOverlay.points.length === 0) {
      m.setData([]);
      m.priceScale().applyOptions({ visible: false });
      return;
    }
    m.setData(toMacroLine(macroOverlay.points));
    m.priceScale().applyOptions({ visible: true });
  }, [macroOverlay]);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
