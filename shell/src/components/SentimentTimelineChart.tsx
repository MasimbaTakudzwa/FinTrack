import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import {
  SENTIMENT_NEGATIVE_THRESHOLD,
  SENTIMENT_POSITIVE_THRESHOLD,
  type SentimentTimeseriesPoint,
} from "../api/client";
import { useResolvedTheme } from "../stores/useSettings";

type Theme = "light" | "dark";

interface SentimentTimelineChartProps {
  points: SentimentTimeseriesPoint[];
  /** Optional zero-line is drawn so positive vs negative is unambiguous. */
  showZeroLine?: boolean;
}

interface Palette {
  bg: string;
  text: string;
  grid: string;
  positive: string;
  neutral: string;
  negative: string;
  zero: string;
}

const PALETTES: Record<Theme, Palette> = {
  light: {
    bg: "#ffffff",
    text: "#3f3f46",
    grid: "#e4e4e7",
    positive: "#10b981",
    neutral: "#a1a1aa",
    negative: "#f43f5e",
    zero: "#52525b",
  },
  dark: {
    bg: "#0a0a0a",
    text: "#a1a1aa",
    grid: "#27272a",
    positive: "#34d399",
    neutral: "#71717a",
    negative: "#fb7185",
    zero: "#a1a1aa",
  },
};

function dateToUtcTimestamp(iso: string): UTCTimestamp {
  // "YYYY-MM-DD" — anchor to midnight UTC so the chart's UTC time scale
  // bins each day in its own slot regardless of the user's local offset.
  return (Date.parse(`${iso}T00:00:00Z`) / 1000) as UTCTimestamp;
}

/**
 * Daily-mean sentiment bars colored by sign — the visual companion to the
 * candle chart on AssetDetail. We use HistogramSeries with a per-point
 * color so positive/negative/neutral days are immediately legible without
 * a legend. A faint zero-line is overlaid for unambiguous polarity.
 *
 * Empty days are simply absent from `points` — the chart leaves a gap
 * rather than misrepresenting "no news" as a neutral score of zero.
 */
export function SentimentTimelineChart({
  points,
  showZeroLine = true,
}: SentimentTimelineChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const histRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const zeroRef = useRef<ISeriesApi<"Line"> | null>(null);
  const theme = useResolvedTheme();

  // Lifecycle: build the chart once, recreate on theme change for crisp
  // palette swaps. Series are created here too so the data effect below
  // can stay narrow.
  useEffect(() => {
    if (!containerRef.current) return undefined;
    const palette = PALETTES[theme];
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: palette.bg },
        textColor: palette.text,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      timeScale: {
        timeVisible: false,
        secondsVisible: false,
        borderColor: palette.grid,
      },
      rightPriceScale: {
        borderColor: palette.grid,
        scaleMargins: { top: 0.15, bottom: 0.15 },
      },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;

    const hist = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "price", precision: 3, minMove: 0.001 },
      priceLineVisible: false,
      lastValueVisible: true,
    });
    histRef.current = hist;

    if (showZeroLine) {
      const zero = chart.addSeries(LineSeries, {
        color: palette.zero,
        lineWidth: 1,
        lineStyle: 2, // dashed
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      zeroRef.current = zero;
    }

    return () => {
      chart.remove();
      chartRef.current = null;
      histRef.current = null;
      zeroRef.current = null;
    };
  }, [theme, showZeroLine]);

  // Data effect — pushes points in whenever they change. Separate from
  // lifecycle so a re-fetch doesn't tear the chart down.
  useEffect(() => {
    const hist = histRef.current;
    if (!hist) return;
    const palette = PALETTES[theme];
    hist.setData(
      points.map((p) => ({
        time: dateToUtcTimestamp(p.date),
        value: p.mean,
        color:
          p.mean >= SENTIMENT_POSITIVE_THRESHOLD
            ? palette.positive
            : p.mean <= SENTIMENT_NEGATIVE_THRESHOLD
              ? palette.negative
              : palette.neutral,
      })),
    );
    if (zeroRef.current && points.length) {
      zeroRef.current.setData([
        { time: dateToUtcTimestamp(points[0].date), value: 0 },
        {
          time: dateToUtcTimestamp(points[points.length - 1].date),
          value: 0,
        },
      ]);
    } else if (zeroRef.current) {
      zeroRef.current.setData([]);
    }
    chartRef.current?.timeScale().fitContent();
  }, [points, theme]);

  return <div ref={containerRef} className="h-40 w-full" />;
}
