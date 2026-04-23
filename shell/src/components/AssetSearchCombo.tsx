/**
 * Asset-search combobox.
 *
 * Renders a search input + dropdown of Yahoo Finance autocomplete hits. Users
 * type "apple" or "bitcoin" and pick from the list — no need to know the
 * ticker. Selection fires `onSelect(hit)` so the caller can drive its own
 * next step (preview, add, etc.).
 *
 * Design notes:
 * - 300ms debounce on typing so we don't hammer `/api/assets/search/` on
 *   every keystroke.
 * - AbortController on each in-flight request, cancelled when the user types
 *   again (stale responses never overwrite fresh ones).
 * - Arrow-key navigation: ArrowDown/ArrowUp moves the active row, Enter
 *   selects it, Escape closes the dropdown (keeps the focus).
 * - Click-outside closes the dropdown — a document-level mousedown listener
 *   that checks containment via `containerRef`.
 * - React 19 `react-hooks/set-state-in-effect` compliance: no synchronous
 *   setState inside effect bodies. State transitions happen in event
 *   handlers (onChange, keyboard, click) and `.then`/`.catch` callbacks.
 */

import { ChevronDown, Loader2, Search } from "lucide-react";
import type React from "react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { ApiError, searchAssets, type SymbolSearchHit } from "../api/client";

interface AssetSearchComboProps {
  /** Fires when the user picks a hit (click or Enter on the active row). */
  onSelect: (hit: SymbolSearchHit) => void;
  /** Placeholder shown when the query is empty. */
  placeholder?: string;
  /** Focus the input on mount. */
  autoFocus?: boolean;
  /** Max hits to show. Clamped server-side to 1-20; default 10. */
  limit?: number;
  /** External label id, wires to the input via aria-labelledby. */
  "aria-labelledby"?: string;
}

export function AssetSearchCombo({
  onSelect,
  placeholder = "Search by name or symbol — e.g. 'apple', 'bitcoin', 'SPY'",
  autoFocus = false,
  limit = 10,
  "aria-labelledby": ariaLabelledBy,
}: AssetSearchComboProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SymbolSearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState<number>(-1);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const listboxId = useId();

  // Clean up any pending timer + request on unmount.
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      controllerRef.current?.abort();
    };
  }, []);

  // Click-outside closes the dropdown. Only bound while open so we aren't
  // paying for a document listener on every search combobox in the tree.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const el = containerRef.current;
      if (!el) return;
      if (!el.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const runSearch = useCallback(
    (q: string) => {
      controllerRef.current?.abort();
      const ctl = new AbortController();
      controllerRef.current = ctl;
      searchAssets(q, { limit, signal: ctl.signal })
        .then((res) => {
          // Ignore responses for stale requests — abort races can still
          // deliver a payload in some browsers.
          if (ctl.signal.aborted) return;
          setResults(res.results);
          setActiveIndex(res.results.length > 0 ? 0 : -1);
          setError(null);
        })
        .catch((err: unknown) => {
          if (ctl.signal.aborted) return;
          if (err instanceof DOMException && err.name === "AbortError") return;
          setResults([]);
          setActiveIndex(-1);
          if (err instanceof ApiError) {
            setError(`Search failed (HTTP ${err.status})`);
          } else if (err instanceof Error) {
            setError(err.message);
          } else {
            setError(String(err));
          }
        })
        .finally(() => {
          if (ctl.signal.aborted) return;
          setLoading(false);
        });
    },
    [limit],
  );

  const onQueryChange = (next: string) => {
    setQuery(next);
    setOpen(true);
    setError(null);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    const trimmed = next.trim();
    if (!trimmed) {
      // Empty query — cancel any pending fetch, clear results.
      controllerRef.current?.abort();
      setResults([]);
      setActiveIndex(-1);
      setLoading(false);
      return;
    }
    setLoading(true);
    timerRef.current = window.setTimeout(() => {
      runSearch(trimmed);
    }, 300);
  };

  const onInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      if (results.length === 0) return;
      e.preventDefault();
      setOpen(true);
      setActiveIndex((i) => (i + 1) % results.length);
      return;
    }
    if (e.key === "ArrowUp") {
      if (results.length === 0) return;
      e.preventDefault();
      setOpen(true);
      setActiveIndex((i) => (i <= 0 ? results.length - 1 : i - 1));
      return;
    }
    if (e.key === "Enter") {
      if (!open || results.length === 0 || activeIndex < 0) return;
      e.preventDefault();
      const hit = results[activeIndex];
      if (hit) onSelect(hit);
      setOpen(false);
      return;
    }
    if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        setOpen(false);
      }
    }
  };

  const onRowClick = (hit: SymbolSearchHit) => {
    onSelect(hit);
    setOpen(false);
  };

  const showDropdown =
    open && (loading || !!error || results.length > 0 || query.trim().length > 0);

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400 dark:text-zinc-500" />
        <input
          type="text"
          role="combobox"
          aria-expanded={showDropdown}
          aria-controls={listboxId}
          aria-activedescendant={
            activeIndex >= 0 ? `${listboxId}-opt-${activeIndex}` : undefined
          }
          aria-labelledby={ariaLabelledBy}
          autoComplete="off"
          spellCheck={false}
          autoFocus={autoFocus}
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={onInputKeyDown}
          onFocus={() => setOpen(true)}
          placeholder={placeholder}
          className="block w-full rounded-md border border-zinc-200 bg-white py-2 pl-9 pr-9 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-500"
        />
        {loading ? (
          <Loader2 className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-zinc-400" />
        ) : (
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400 dark:text-zinc-500" />
        )}
      </div>

      {showDropdown && (
        <div
          id={listboxId}
          role="listbox"
          className="absolute left-0 right-0 top-full z-20 mt-1 max-h-72 overflow-y-auto rounded-md border border-zinc-200 bg-white shadow-lg dark:border-zinc-800 dark:bg-zinc-950"
        >
          {error && (
            <div className="px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
              {error}
            </div>
          )}
          {!error && loading && results.length === 0 && (
            <div className="px-3 py-2 text-xs text-zinc-500 dark:text-zinc-400">
              Searching…
            </div>
          )}
          {!error && !loading && results.length === 0 && query.trim() && (
            <div className="px-3 py-2 text-xs text-zinc-500 dark:text-zinc-400">
              No matches for <span className="font-mono">{query.trim()}</span>.
              {" "}Try a different name, or the exact Yahoo symbol.
            </div>
          )}
          {results.map((hit, idx) => (
            <button
              key={`${hit.symbol}-${idx}`}
              id={`${listboxId}-opt-${idx}`}
              role="option"
              aria-selected={idx === activeIndex}
              type="button"
              // Use onMouseDown (instead of onClick) so we fire BEFORE the
              // input loses focus — prevents a click-outside race where the
              // dropdown closes before the selection registers.
              onMouseDown={(e) => {
                e.preventDefault();
                onRowClick(hit);
              }}
              onMouseEnter={() => setActiveIndex(idx)}
              className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition-colors ${
                idx === activeIndex
                  ? "bg-indigo-50 dark:bg-indigo-950/60"
                  : "hover:bg-zinc-50 dark:hover:bg-zinc-900"
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    {hit.symbol}
                  </span>
                  <span className="truncate text-xs text-zinc-600 dark:text-zinc-300">
                    {hit.name}
                  </span>
                </div>
                {hit.exchange && (
                  <div className="mt-0.5 text-[11px] text-zinc-400 dark:text-zinc-500">
                    {hit.exchange}
                  </div>
                )}
              </div>
              <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                {hit.asset_type}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

