import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  type Asset,
  type PriceSeries,
  getPriceSeries,
  listAssets,
} from "../api/client";
import { AssetCard } from "../components/AssetCard";

interface LoadState {
  assets: Asset[];
  series: Record<string, PriceSeries>;
  errors: Record<string, string>;
  assetsError: string | null;
  loading: boolean;
}

const INITIAL: LoadState = {
  assets: [],
  series: {},
  errors: {},
  assetsError: null,
  loading: true,
};

async function loadAll(
  signal: AbortSignal,
): Promise<Omit<LoadState, "loading">> {
  const assets = await listAssets({ signal });
  const series: Record<string, PriceSeries> = {};
  const errors: Record<string, string> = {};
  await Promise.all(
    assets.map(async (a) => {
      try {
        series[a.symbol] = await getPriceSeries(a.symbol, {
          limit: 60,
          signal,
        });
      } catch (err) {
        errors[a.symbol] = err instanceof Error ? err.message : String(err);
      }
    }),
  );
  return { assets, series, errors, assetsError: null };
}

export function Dashboard() {
  const [state, setState] = useState<LoadState>(INITIAL);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const next = await loadAll(controller.signal);
        if (!cancelled) setState({ ...next, loading: false });
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        setState({
          ...INITIAL,
          loading: false,
          assetsError: err instanceof Error ? err.message : String(err),
        });
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [tick]);

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    setTick((t) => t + 1);
  };

  return (
    <div className="p-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            Watchlist
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {state.loading
              ? "Loading assets…"
              : `${state.assets.length} active asset${state.assets.length === 1 ? "" : "s"}`}
          </p>
        </div>
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

      {state.assetsError && (
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          Failed to load assets: {state.assetsError}
        </div>
      )}

      {!state.loading && state.assets.length === 0 && !state.assetsError && (
        <div className="rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-400">
          No active assets yet. Run the sidecar with seed enabled to populate defaults.
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {state.assets.map((a) => (
          <AssetCard
            key={a.id}
            asset={a}
            series={state.series[a.symbol] ?? null}
            loading={state.loading}
            error={state.errors[a.symbol] ?? null}
          />
        ))}
      </div>
    </div>
  );
}
