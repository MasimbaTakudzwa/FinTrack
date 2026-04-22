/**
 * "Add asset" modal.
 *
 * Two-step flow:
 *   1. User types a symbol + clicks Lookup (or presses Enter) → POST /api/assets/lookup/
 *      Shows a preview card with the resolved name, type, exchange, currency.
 *   2. User clicks Add → POST /api/assets/ → persist + kick off one-shot ingest.
 *
 * Why not auto-add on first Enter? Yahoo sometimes resolves typos to something
 * you didn't mean (e.g. "APL" resolving to a delisted ADR). The preview step
 * lets the user double-check before committing.
 */

import { Plus, Search, X } from "lucide-react";
import { useEffect, useState } from "react";

import {
  type Asset,
  type AssetLookup,
  ApiError,
  createAsset,
  lookupAsset,
} from "../api/client";

interface AddAssetModalProps {
  onClose: () => void;
  /** Called after a successful Add. The caller can refresh its asset list. */
  onCreated: (asset: Asset, barsIngested: number, addedToWatchlist: boolean) => void;
  /** Whether to also add the new asset to the default watchlist. */
  addToDefaultWatchlist?: boolean;
}

export function AddAssetModal({
  onClose,
  onCreated,
  addToDefaultWatchlist = true,
}: AddAssetModalProps) {
  const [symbol, setSymbol] = useState("");
  const [preview, setPreview] = useState<AssetLookup | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const normalised = symbol.trim().toUpperCase();
  // Invalidate the preview card whenever the typed symbol diverges from it.
  const previewMatchesInput = preview?.symbol === normalised;

  const doLookup = () => {
    if (!normalised || lookingUp) return;
    setLookingUp(true);
    setError(null);
    setPreview(null);
    lookupAsset(normalised)
      .then((lu) => setPreview(lu))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 404) {
          setError(`Symbol "${normalised}" not found on Yahoo Finance.`);
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLookingUp(false));
  };

  const doAdd = () => {
    if (!preview || adding) return;
    setAdding(true);
    setError(null);
    createAsset({
      symbol: preview.symbol,
      add_to_default_watchlist: addToDefaultWatchlist,
    })
      .then((res) => {
        onCreated(res.asset, res.bars_ingested, res.added_to_watchlist);
        onClose();
      })
      .catch((err: unknown) => {
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

  const onSymbolKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (preview && previewMatchesInput) {
        doAdd();
      } else {
        doLookup();
      }
    }
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
              <h2 className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
                Track a new asset
              </h2>
              <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                Any symbol Yahoo Finance knows — stocks, ETFs, crypto, futures, FX.
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
            <label
              htmlFor="add-asset-symbol"
              className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
            >
              Symbol
            </label>
            <div className="mt-1.5 flex gap-2">
              <input
                id="add-asset-symbol"
                type="text"
                autoFocus
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                onKeyDown={onSymbolKey}
                maxLength={32}
                className="block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 font-mono text-sm uppercase tracking-wide text-zinc-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                placeholder="AAPL, TSLA, BTC-USD, GC=F, EURUSD=X…"
              />
              <button
                type="button"
                onClick={doLookup}
                disabled={!normalised || lookingUp}
                className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-3 py-2 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
              >
                <Search className="h-3.5 w-3.5" />
                {lookingUp ? "Looking…" : "Lookup"}
              </button>
            </div>
            <p className="mt-1 text-[11px] text-zinc-400 dark:text-zinc-500">
              Tip: Yahoo suffixes are supported. <code>-USD</code> for crypto,{" "}
              <code>=F</code> for futures, <code>=X</code> for FX pairs.
            </p>
          </div>

          {preview && previewMatchesInput && (
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
            disabled={!preview || !previewMatchesInput || adding}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:hover:bg-indigo-600"
          >
            {adding ? "Adding…" : "Add asset"}
          </button>
        </div>
      </div>
    </div>
  );
}
