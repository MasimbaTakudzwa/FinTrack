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
import type { Forecast, PricePoint } from "../api/client";

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
}

function toCandles(points: PricePoint[]): CandlestickData<UTCTimestamp>[] {
  return points.map((p) => ({
    time: (Date.parse(p.timestamp) / 1000) as UTCTimestamp,
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
      time: (Date.parse(p.timestamp) / 1000) as UTCTimestamp,
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
      };
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
 * Forecast overlay series handles, held by the outer chart effect and
 * cleaned up together. We keep five series: a dashed median, plus four
 * bounded lines tracing the 80%/95% CI edges. Shaded bands would be
 * cleaner visually but lightweight-charts v5 has no first-class "area
 * between two curves" primitive, and the edge-line approach is still
 * unambiguous — 95% lines sit outside 80% lines.
 */
interface ForecastSeries {
  median: ISeriesApi<"Line">;
  lower80: ISeriesApi<"Line">;
  upper80: ISeriesApi<"Line">;
  lower95: ISeriesApi<"Line">;
  upper95: ISeriesApi<"Line">;
}

export function CandleChart({
  points,
  dark,
  height = 380,
  measure,
  forecast,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const forecastRef = useRef<ForecastSeries | null>(null);
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
    const forecastLower80 = chart.addSeries(LineSeries, {
      ...commonLineOpts,
      color: p.forecastBand80,
      lineWidth: 1,
    });
    const forecastUpper80 = chart.addSeries(LineSeries, {
      ...commonLineOpts,
      color: p.forecastBand80,
      lineWidth: 1,
    });
    const forecastLower95 = chart.addSeries(LineSeries, {
      ...commonLineOpts,
      color: p.forecastBand95,
      lineWidth: 1,
    });
    const forecastUpper95 = chart.addSeries(LineSeries, {
      ...commonLineOpts,
      color: p.forecastBand95,
      lineWidth: 1,
    });

    chartRef.current = chart;
    candleRef.current = candle;
    volumeRef.current = volume;
    markersRef.current = markers;
    forecastRef.current = {
      median: forecastMedian,
      lower80: forecastLower80,
      upper80: forecastUpper80,
      lower95: forecastLower95,
      upper95: forecastUpper95,
    };

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
    };
  }, [dark, height]);

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

  // Reflect measure-point markers on the chart.
  useEffect(() => {
    if (!markersRef.current) return;
    const p = palette(dark);
    const pts: MeasurePoint[] = [];
    if (measure?.first) pts.push(measure.first);
    if (measure?.second) pts.push(measure.second);
    const markers: SeriesMarker<Time>[] = pts.map((pt, idx) => ({
      time: pt.time,
      position: "inBar",
      shape: "circle",
      color: p.marker,
      text: idx === 0 ? "A" : "B",
    }));
    markersRef.current.setMarkers(markers);
  }, [measure?.first, measure?.second, dark]);

  // Push forecast data (or empty arrays to hide) into the overlay series.
  // Guarded by the chart-created ref so hot-reload re-renders don't crash on
  // a transiently-null handle.
  useEffect(() => {
    const f = forecastRef.current;
    if (!f) return;
    if (!forecast) {
      f.median.setData([]);
      f.lower80.setData([]);
      f.upper80.setData([]);
      f.lower95.setData([]);
      f.upper95.setData([]);
      return;
    }
    f.median.setData(toLine(forecast, (p) => p.yhat));
    f.lower80.setData(toLine(forecast, (p) => p.lower_80));
    f.upper80.setData(toLine(forecast, (p) => p.upper_80));
    f.lower95.setData(toLine(forecast, (p) => p.lower_95));
    f.upper95.setData(toLine(forecast, (p) => p.upper_95));
    chartRef.current?.timeScale().fitContent();
  }, [forecast]);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
