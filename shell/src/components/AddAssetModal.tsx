/**
 * "Add asset" modal.
 *
 * Two-step flow:
 *   1. User types in the search combobox → Yahoo Finance autocomplete →
 *      picks a hit. This resolves the canonical ticker without requiring
 *      the user to know it ahead of time.
 *   2. On pick, the modal fetches `POST /api/assets/lookup/` for the full
 *      preview (currency + final name). User clicks Add → POST /api/assets/
 *      persists + kicks off a one-shot ingest.
 *
 * We still keep the preview step — it confirms the currency/exchange the
 * user is about to persist (e.g. Apple is listed on many exchanges with
 * different suffixes; the user wants to see "USD / NASDAQ" before committing).
 * Power users typing a full ticker like "SPY" will see SPY as the top hit
 * and can press Enter to select it, preserving the old one-shot feel.
 */

import { Plus, X } from "lucide-react";
import { useEffect, useState } from "react";

import {
  type Asset,
  type AssetLookup,
  ApiError,
  createAsset,
  lookupAsset,
  type SymbolSearchHit,
} from "../api/client";
import { AssetSearchCombo } from "./AssetSearchCombo";

interface AddAssetModalProps {
  onClose: () => void;
  /**
   * Called after a successful Add. The caller can refresh its asset list.
   * ``newlyAdded`` distinguishes a fresh yfinance resolution from an
   * idempotent "this asset was already tracked" outcome — the caller can
   * word its success banner accordingly.
   */
  onCreated: (
    asset: Asset,
    barsIngested: number,
    addedToWatchlist: boolean,
    newlyAdded: boolean,
  ) => void;
  /** Whether to also add the new asset to the default watchlist. */
  addToDefaultWatchlist?: boolean;
  /**
   * When set, the backend also links the asset to this watchlist on create.
   * Use this when the modal is launched from the "Track new…" button on a
   * non-default watchlist — it makes the whole flow idempotent and avoids a
   * follow-up ``addWatchlistItem`` call that would race the POST response.
   */
  watchlistId?: number | null;
}

export function AddAssetModal({
  onClose,
  onCreated,
  addToDefaultWatchlist = true,
  watchlistId = null,
}: AddAssetModalProps) {
  const [selectedHit, setSelectedHit] = useState<SymbolSearchHit | null>(null);
  const [preview, setPreview] = useState<AssetLookup | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const onPickHit = (hit: SymbolSearchHit) => {
    setSelectedHit(hit);
    setError(null);
    setPreview(null);
    setPreviewLoading(true);
    lookupAsset(hit.symbol)
      .then((lu) => setPreview(lu))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 404) {
          setError(
            `Yahoo Finance couldn't resolve "${hit.symbol}" right now. Try another result.`,
          );
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError(String(err));
        }
      })
      .finally(() => setPreviewLoading(false));
  };

  const doAdd = () => {
    if (!preview || adding) return;
    setAdding(true);
    setError(null);
    createAsset({
      symbol: preview.symbol,
      add_to_default_watchlist: addToDefaultWatchlist,
      watchlist_id: watchlistId,
    })
      .then((res) => {
        onCreated(
          res.asset,
          res.bars_ingested,
          res.added_to_watchlist,
          res.newly_added,
        );
        onClose();
      })
      .catch((err: unknown) => {
        // 409 is no longer raised for already-tracked symbols (POST is
        // idempotent), but keep a friendly fallback in case the backend
        // ever reintroduces it for a different conflict.
        if (err instanceof ApiError && err.status === 409) {
          setError(`"${preview.symbol}" is already tracked.`);
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError(String(err));
        }
      })
      .finally(() => setAdding(false));
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
              <Plus className="h-4 w-4" />
            </div>
            <div>
              <h2
                id="add-asset-modal-title"
                className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100"
              >
                Track a new asset
              </h2>
              <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                Search Yahoo Finance by name or symbol — stocks, ETFs, crypto, futures, FX.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          <div>
            <div
              id="add-asset-search-label"
              className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
            >
              Search
            </div>
            <div className="mt-1.5">
              <AssetSearchCombo
                onSelect={onPickHit}
                autoFocus
                aria-labelledby="add-asset-search-label"
              />
            </div>
            <p className="mt-1.5 text-[11px] text-zinc-400 dark:text-zinc-500">
              Try "apple", "bitcoin", "gold futures", or an exact symbol like{" "}
              <code>SPY</code>.
            </p>
          </div>

          {selectedHit && previewLoading && (
            <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3 text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
              Loading preview for{" "}
              <span className="font-mono text-zinc-700 dark:text-zinc-300">
                {selectedHit.symbol}
              </span>
              …
            </div>
          )}

          {preview && (
            <div className="rounded-md border border-indigo-200 bg-indigo-50 p-3 dark:border-indigo-900 dark:bg-indigo-950/50">
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  {preview.symbol}
                </span>
                <span className="rounded-full bg-indigo-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-indigo-700 dark:text-indigo-300">
                  {preview.asset_type}
                </span>
              </div>
              <div className="mt-1 text-sm text-zinc-700 dark:text-zinc-200">
                {preview.name}
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 text-[11px] text-zinc-500 dark:text-zinc-400">
                {preview.exchange && (
                  <div>
                    <dt className="inline">Exchange: </dt>
                    <dd className="inline text-zinc-700 dark:text-zinc-300">
                      {preview.exchange}
                    </dd>
                  </div>
                )}
                {preview.currency && (
                  <div>
                    <dt className="inline">Currency: </dt>
                    <dd className="inline text-zinc-700 dark:text-zinc-300">
                      {preview.currency}
                    </dd>
                  </div>
                )}
              </dl>
            </div>
          )}

          {error && (
            <div className="rounded-md border border-rose-200 bg-rose-50 p-2 text-xs text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-zinc-200 px-5 py-3 dark:border-zinc-800">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={doAdd}
            disabled={!preview || previewLoading || adding}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:hover:bg-indigo-600"
          >
            {adding ? "Adding…" : preview ? `Add ${preview.symbol}` : "Add asset"}
          </button>
        </div>
      </div>
    </div>
  );
}
