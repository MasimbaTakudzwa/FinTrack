import { useEffect, useRef } from "react";
import {
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from "lightweight-charts";
import type { MacroDataPoint } from "../api/client";

interface Props {
  points: MacroDataPoint[];
  dark: boolean;
  height?: number;
}

function palette(dark: boolean) {
  return dark
    ? {
        background: "transparent",
        text: "#d4d4d8",
        grid: "#27272a",
        border: "#3f3f46",
        line: "#818cf8",
      }
    : {
        background: "transparent",
        text: "#3f3f46",
        grid: "#e4e4e7",
        border: "#d4d4d8",
        line: "#4f46e5",
      };
}

function toLineData(points: MacroDataPoint[]): LineData<UTCTimestamp>[] {
  // `date` is "YYYY-MM-DD" — coerce to UTC midnight so the time-scale treats
  // it as a single calendar day regardless of the user's local offset.
  return points.map((p) => ({
    time: (Date.parse(`${p.date}T00:00:00Z`) / 1000) as UTCTimestamp,
    value: Number(p.value),
  }));
}

/**
 * Line chart for a single macro series (CPI, unemployment rate, etc.).
 *
 * Sibling of `CandleChart` — same lifecycle (createChart once, setData on
 * points change, clean up on unmount) but uses `LineSeries` because FRED
 * observations are single-valued daily/monthly/quarterly readings, not OHLC.
 */
export function MacroLineChart({ points, dark, height = 380 }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineRef = useRef<ISeriesApi<"Line"> | null>(null);

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
        timeVisible: false,
        secondsVisible: false,
      },
      crosshair: { mode: 1 },
    });

    const line = chart.addSeries(LineSeries, {
      color: p.line,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });

    chartRef.current = chart;
    lineRef.current = line;

    return () => {
      chart.remove();
      chartRef.current = null;
      lineRef.current = null;
    };
  }, [dark, height]);

  useEffect(() => {
    if (!lineRef.current) return;
    lineRef.current.setData(toLineData(points));
    chartRef.current?.timeScale().fitContent();
  }, [points]);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
