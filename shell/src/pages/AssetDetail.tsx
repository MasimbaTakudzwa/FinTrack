import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowDownRight,
  ArrowLeft,
  ArrowUpRight,
  Bell,
  Minus,
  Newspaper,
  RefreshCw,
} from "lucide-react";
import {
  type Article,
  type Asset,
  type PriceAlert,
  type PricePoint,
  type PriceSeries,
  getPriceSeries,
  listAssets,
  listNews,
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

function fmtPrice(n: number): string {
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(2);
  return n.toFixed(4);
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
          getPriceSeries(symbol, { limit: 500, signal: controller.signal }).catch(
            (e: unknown) => {
              // 404 here means the symbol is valid but has no asset record — treat as notFound later
              throw e;
            },
          ),
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
        <AssetBody asset={state.asset} series={state.series} dark={resolved === "dark"} />
      )}
    </div>
  );
}

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
  const last = series.points[series.points.length - 1];
  const lastClose = last ? Number(last.close) : null;
  const changePct = latestChange(series.points);
  const dir: "up" | "down" | "flat" =
    changePct === null ? "flat" : changePct > 0 ? "up" : changePct < 0 ? "down" : "flat";
  const tone = {
    up: "text-emerald-600 dark:text-emerald-400",
    down: "text-rose-600 dark:text-rose-400",
    flat: "text-zinc-500 dark:text-zinc-400",
  }[dir];

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
            <div className={`mt-0.5 inline-flex items-center gap-1 text-sm font-semibold ${tone}`}>
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
          {series.count === 0 ? (
            <div className="flex h-[380px] items-center justify-center text-sm text-zinc-500 dark:text-zinc-400">
              No bars available for {asset.symbol} yet.
            </div>
          ) : (
            <CandleChart points={series.points} dark={dark} />
          )}
          <div className="mt-2 flex items-center justify-between text-[11px] text-zinc-400 dark:text-zinc-500">
            <span>{series.count} bars</span>
            {series.points.length > 0 && (
              <span>
                {series.points[0].timestamp.replace("T", " ").slice(0, 16)} →{" "}
                {series.points[series.points.length - 1].timestamp
                  .replace("T", " ")
                  .slice(0, 16)}{" "}
                UTC
              </span>
            )}
          </div>
        </div>

        <aside className="flex flex-col gap-4">
          <PricePanel last={last} />
          <NewsPanel key={asset.symbol} symbol={asset.symbol} />
        </aside>
      </div>
    </div>
  );
}

function PricePanel({ last }: { last: PricePoint | undefined }) {
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
