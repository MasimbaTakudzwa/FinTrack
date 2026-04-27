import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ListPlus, Plus, RefreshCw } from "lucide-react";
import {
  ApiError,
  type Asset,
  type PriceSeries,
  type WatchlistDetail,
  getDefaultWatchlist,
  getPriceSeries,
  listAssets,
} from "../api/client";
import { AddAssetModal } from "../components/AddAssetModal";
import { AssetCard } from "../components/AssetCard";
import { PortfolioSummaryStrip } from "../components/PortfolioSummaryStrip";

interface LoadState {
  /** Ordered list of assets to render. Driven by the default watchlist when present. */
  assets: Asset[];
  watchlistName: string | null;
  /** True when no default watchlist exists (vs. exists-but-empty). */
  noDefault: boolean;
  series: Record<string, PriceSeries>;
  errors: Record<string, string>;
  assetsError: string | null;
  loading: boolean;
}

const INITIAL: LoadState = {
  assets: [],
  watchlistName: null,
  noDefault: false,
  series: {},
  errors: {},
  assetsError: null,
  loading: true,
};

async function loadAll(
  signal: AbortSignal,
): Promise<Omit<LoadState, "loading">> {
  // Pull both the default watchlist and every known asset. The watchlist drives
  // ordering and filters; the full asset list is the fallback when there isn't
  // one yet (first-run, before seed_default_watchlist runs).
  let detail: WatchlistDetail | null = null;
  let noDefault = false;
  try {
    detail = await getDefaultWatchlist(signal);
  } catch (err) {
    // A 404 means no default exists yet — show all active assets so the
    // dashboard is never blank before migrations+seed complete.
    if (err instanceof ApiError && err.status === 404) {
      noDefault = true;
    } else {
      throw err;
    }
  }

  const allAssets = await listAssets({ activeOnly: false, signal });

  let assets: Asset[];
  if (detail) {
    const byId = new Map(allAssets.map((a) => [a.id, a]));
    assets = detail.items
      .map((item) => byId.get(item.asset_id))
      .filter((a): a is Asset => a !== undefined);
  } else {
    assets = allAssets.filter((a) => a.is_active);
  }

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
  return {
    assets,
    watchlistName: detail?.name ?? null,
    noDefault,
    series,
    errors,
    assetsError: null,
  };
}

export function Dashboard() {
  const [state, setState] = useState<LoadState>(INITIAL);
  const [tick, setTick] = useState(0);
  const [addOpen, setAddOpen] = useState(false);
  const [addBanner, setAddBanner] = useState<string | null>(null);

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

  const title = state.watchlistName ?? "Watchlist";
  const subtitle = state.loading
    ? "Loading assets…"
    : state.noDefault
      ? `${state.assets.length} active asset${state.assets.length === 1 ? "" : "s"} · no default watchlist`
      : state.watchlistName
        ? `${state.assets.length} asset${state.assets.length === 1 ? "" : "s"} on "${state.watchlistName}"`
        : `${state.assets.length} active asset${state.assets.length === 1 ? "" : "s"}`;

  return (
    <div className="p-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            {title}
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">{subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
          >
            <Plus className="h-3.5 w-3.5" />
            Add asset
          </button>
          <Link
            to="/watchlists"
            className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <ListPlus className="h-3.5 w-3.5" />
            Manage
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
      </div>

      {addBanner && (
        <div className="mb-4 flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">
          <span>{addBanner}</span>
          <button
            type="button"
            onClick={() => setAddBanner(null)}
            className="text-xs font-medium underline hover:no-underline"
          >
            Dismiss
          </button>
        </div>
      )}

      <PortfolioSummaryStrip />

      {state.assetsError && (
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          Failed to load assets: {state.assetsError}
        </div>
      )}

      {!state.loading && state.assets.length === 0 && !state.assetsError && (
        <div className="rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-400">
          {state.noDefault ? (
            <>
              No active assets yet. Run the sidecar with seed enabled to populate defaults.
            </>
          ) : (
            <>
              <p>Your default watchlist is empty.</p>
              <Link
                to="/watchlists"
                className="mt-3 inline-flex items-center gap-2 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
              >
                <ListPlus className="h-3.5 w-3.5" />
                Add assets to "{state.watchlistName ?? "Default"}"
              </Link>
            </>
          )}
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

      {addOpen && (
        <AddAssetModal
          onClose={() => setAddOpen(false)}
          onCreated={(asset, bars, added, newlyAdded) => {
            const head = newlyAdded
              ? `Added ${asset.symbol} (${asset.name})`
              : `${asset.symbol} was already tracked`;
            const parts = [head];
            if (bars > 0) parts.push(`${bars} bars ingested`);
            if (added) parts.push("on default watchlist");
            setAddBanner(parts.join(" · "));
            // Refresh the dashboard so the (possibly new) asset shows up.
            setTick((t) => t + 1);
          }}
        />
      )}
    </div>
  );
}
