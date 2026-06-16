import { barTimeSeconds, type PricePoint } from "../api/client";

/**
 * Client-side technical-analysis overlays computed from the daily close series.
 *
 * These are *descriptive* — they summarise where price has been and its recent
 * spread — not predictions. Kept out of the forecast model deliberately.
 */

export interface LinePt {
  time: number;
  value: number;
}

export interface ChannelData {
  mid: LinePt[];
  upper: LinePt[];
  lower: LinePt[];
}

/**
 * Linear regression channel: least-squares trend line through the closes plus
 * parallel rails at ±k·(residual standard deviation). The classic trader's
 * "regression channel" drawing.
 */
export function regressionChannel(points: PricePoint[], k = 2): ChannelData | null {
  const n = points.length;
  if (n < 3) return null;
  const ys = points.map((p) => Number(p.close));
  const meanX = (n - 1) / 2;
  const meanY = ys.reduce((a, b) => a + b, 0) / n;
  let sxy = 0;
  let sxx = 0;
  for (let i = 0; i < n; i++) {
    sxy += (i - meanX) * (ys[i] - meanY);
    sxx += (i - meanX) ** 2;
  }
  if (sxx === 0) return null;
  const slope = sxy / sxx;
  const intercept = meanY - slope * meanX;
  const fit = (i: number) => intercept + slope * i;

  let ss = 0;
  for (let i = 0; i < n; i++) ss += (ys[i] - fit(i)) ** 2;
  const band = k * Math.sqrt(ss / n);

  const mid: LinePt[] = [];
  const upper: LinePt[] = [];
  const lower: LinePt[] = [];
  for (let i = 0; i < n; i++) {
    const t = barTimeSeconds(points[i].timestamp);
    const m = fit(i);
    mid.push({ time: t, value: m });
    upper.push({ time: t, value: m + band });
    lower.push({ time: t, value: m - band });
  }
  return { mid, upper, lower };
}

/**
 * Bollinger bands: rolling SMA(period) ± k·(rolling standard deviation).
 * Returns null until there are at least `period` closes.
 */
export function bollingerBands(
  points: PricePoint[],
  period = 20,
  k = 2,
): ChannelData | null {
  const n = points.length;
  if (n < period) return null;
  const closes = points.map((p) => Number(p.close));
  const mid: LinePt[] = [];
  const upper: LinePt[] = [];
  const lower: LinePt[] = [];
  for (let i = period - 1; i < n; i++) {
    const win = closes.slice(i - period + 1, i + 1);
    const mean = win.reduce((a, b) => a + b, 0) / period;
    const variance = win.reduce((a, b) => a + (b - mean) ** 2, 0) / period;
    const sd = Math.sqrt(variance);
    const t = barTimeSeconds(points[i].timestamp);
    mid.push({ time: t, value: mean });
    upper.push({ time: t, value: mean + k * sd });
    lower.push({ time: t, value: mean - k * sd });
  }
  return { mid, upper, lower };
}
