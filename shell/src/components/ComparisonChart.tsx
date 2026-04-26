import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from "lightweight-charts";
import { useResolvedTheme } from "../stores/useSettings";

export interface ComparisonSeries {
  symbol: string;
  /** Color used for the line — picked by the parent so legend matches. */
  color: string;
  /** Pre-normalized points (caller divides each close by the first close
   *  in the window and multiplies by 100). */
  points: { time: UTCTimestamp; value: number }[];
}

interface Props {
  series: ComparisonSeries[];
}

interface Palette {
  bg: string;
  text: string;
  grid: string;
  border: string;
}

function palette(theme: "light" | "dark"): Palette {
  return theme === "dark"
    ? {
        bg: "#0a0a0a",
        text: "#d4d4d8",
        grid: "#27272a",
        border: "#3f3f46",
      }
    : {
        bg: "#ffffff",
        text: "#3f3f46",
        grid: "#e4e4e7",
        border: "#d4d4d8",
      };
}

/**
 * Multi-line comparison chart. Each series is normalized to start at 100
 * on the leftmost visible date so wildly different price levels (e.g. SPY
 * at ~$500 vs BTC at ~$60k) compare meaningfully.
 *
 * The chart is recreated on theme change for crisp palette swaps;
 * series creation happens inside the lifecycle effect so adding/removing
 * symbols rebuilds the right number of LineSeries.
 */
export function ComparisonChart({ series }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRefs = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const theme = useResolvedTheme();

  // Lifecycle — chart is rebuilt when theme changes (so colors update
  // crisply) or when the series list churns (different symbol set
  // requires different series count).
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
        borderColor: p.border,
        timeVisible: false,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: p.border,
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;

    const map = new Map<string, ISeriesApi<"Line">>();
    for (const s of series) {
      const line = chart.addSeries(LineSeries, {
        color: s.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: true,
      });
      line.setData(s.points as LineData<UTCTimestamp>[]);
      map.set(s.symbol, line);
    }
    seriesRefs.current = map;
    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRefs.current = new Map();
    };
    // The series array drives the chart's series count, so we recreate
    // when its membership changes. Comparison via `JSON.stringify` of the
    // symbol/color list keeps the dep stable when only point data
    // changes — the data effect below handles updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme, series.map((s) => `${s.symbol}|${s.color}`).join(",")]);

  // Data effect — updates point data without rebuilding the chart.
  useEffect(() => {
    for (const s of series) {
      const line = seriesRefs.current.get(s.symbol);
      if (line) line.setData(s.points as LineData<UTCTimestamp>[]);
    }
    chartRef.current?.timeScale().fitContent();
  }, [series]);

  return <div ref={containerRef} className="h-96 w-full" />;
}
