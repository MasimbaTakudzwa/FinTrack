import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

/**
 * A series primitive that fills the forecast confidence cone.
 *
 * lightweight-charts v5 has no first-class "area between two curves" series, so
 * we draw the 95% and 80% bands as filled polygons directly on the pane canvas,
 * using the chart's time/price coordinate converters. The median line stays a
 * normal LineSeries in CandleChart — this primitive only paints the shaded
 * tolerance bands behind it.
 */

export interface FanPoint {
  time: UTCTimestamp;
  lower95: number;
  upper95: number;
  lower80: number;
  upper80: number;
}

export interface FanColors {
  band95: string;
  band80: string;
}

interface BitmapScope {
  context: CanvasRenderingContext2D;
  horizontalPixelRatio: number;
  verticalPixelRatio: number;
}

interface DrawTarget {
  useBitmapCoordinateSpace(cb: (scope: BitmapScope) => void): void;
}

interface Projected {
  x: number;
  u95: number;
  l95: number;
  u80: number;
  l80: number;
}

export class ForecastFan implements ISeriesPrimitive<Time> {
  private _data: FanPoint[] = [];
  private _colors: FanColors;
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<"Candlestick"> | null = null;
  private _requestUpdate?: () => void;
  private readonly _paneView: ForecastFanPaneView;

  constructor(colors: FanColors) {
    this._colors = colors;
    this._paneView = new ForecastFanPaneView(this);
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this._chart = param.chart;
    this._series = param.series as ISeriesApi<"Candlestick">;
    this._requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this._chart = null;
    this._series = null;
    this._requestUpdate = undefined;
  }

  setData(data: FanPoint[]): void {
    this._data = data;
    this._requestUpdate?.();
  }

  setColors(colors: FanColors): void {
    this._colors = colors;
    this._requestUpdate?.();
  }

  updateAllViews(): void {}

  paneViews(): ForecastFanPaneView[] {
    return [this._paneView];
  }

  /** Project the data to media-space pixel coordinates (null-safe). */
  project(): Projected[] {
    const chart = this._chart;
    const series = this._series;
    if (!chart || !series || this._data.length === 0) return [];
    const ts = chart.timeScale();
    const out: Projected[] = [];
    for (const d of this._data) {
      const x = ts.timeToCoordinate(d.time);
      const u95 = series.priceToCoordinate(d.upper95);
      const l95 = series.priceToCoordinate(d.lower95);
      const u80 = series.priceToCoordinate(d.upper80);
      const l80 = series.priceToCoordinate(d.lower80);
      if (x == null || u95 == null || l95 == null || u80 == null || l80 == null) {
        continue;
      }
      out.push({ x, u95, l95, u80, l80 });
    }
    return out;
  }

  colors(): FanColors {
    return this._colors;
  }
}

class ForecastFanPaneView {
  private readonly _renderer: ForecastFanRenderer;

  constructor(source: ForecastFan) {
    this._renderer = new ForecastFanRenderer(source);
  }

  zOrder(): "bottom" {
    return "bottom";
  }

  renderer(): ForecastFanRenderer {
    return this._renderer;
  }
}

class ForecastFanRenderer {
  private readonly _source: ForecastFan;

  constructor(source: ForecastFan) {
    this._source = source;
  }

  draw(target: DrawTarget): void {
    const pts = this._source.project();
    if (pts.length < 2) return;
    const { band95, band80 } = this._source.colors();

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;

      const fillBand = (
        upper: (p: Projected) => number,
        lower: (p: Projected) => number,
        color: string,
      ) => {
        ctx.beginPath();
        ctx.moveTo(pts[0].x * hr, upper(pts[0]) * vr);
        for (const p of pts) ctx.lineTo(p.x * hr, upper(p) * vr);
        for (let i = pts.length - 1; i >= 0; i--) {
          ctx.lineTo(pts[i].x * hr, lower(pts[i]) * vr);
        }
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();
      };

      fillBand((p) => p.u95, (p) => p.l95, band95);
      fillBand((p) => p.u80, (p) => p.l80, band80);
    });
  }
}
