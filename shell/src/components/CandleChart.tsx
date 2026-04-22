import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { PricePoint } from "../api/client";

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
      }
    : {
        background: "transparent",
        text: "#3f3f46",
        grid: "#e4e4e7",
        border: "#d4d4d8",
        up: "#059669",
        down: "#e11d48",
        marker: "#4f46e5",
      };
}

export function CandleChart({ points, dark, height = 380, measure }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
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

    chartRef.current = chart;
    candleRef.current = candle;
    volumeRef.current = volume;
    markersRef.current = markers;

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

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
