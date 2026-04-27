/**
 * Alert-create modal.
 *
 * Opened from AssetDetail via the "Create alert" button. Renders a simple form
 * with threshold + direction + optional note. On success, calls ``onCreated``
 * so the caller can refresh any inline alert state (list badge, etc.).
 */

import { Bell, X } from "lucide-react";
import { useEffect, useState } from "react";

import {
  type AlertDirection,
  type AlertMetric,
  type PriceAlert,
  createAlert,
} from "../api/client";

interface AlertCreateModalProps {
  assetId: number;
  symbol: string;
  assetName: string;
  /** Latest known close — used to prefill a reasonable-looking threshold. */
  lastPrice: number | null;
  onClose: () => void;
  onCreated: (alert: PriceAlert) => void;
}

const SENTIMENT_WINDOW_OPTIONS: { value: number; label: string }[] = [
  { value: 1, label: "1 day" },
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
];

export function AlertCreateModal({
  assetId,
  symbol,
  assetName,
  lastPrice,
  onClose,
  onCreated,
}: AlertCreateModalProps) {
  const [metric, setMetric] = useState<AlertMetric>("price");
  const [threshold, setThreshold] = useState<string>(
    lastPrice !== null ? lastPrice.toFixed(2) : "",
  );
  const [direction, setDirection] = useState<AlertDirection>("above");
  const [windowDays, setWindowDays] = useState<number>(7);
  const [note, setNote] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onMetricChange = (m: AlertMetric) => {
    setMetric(m);
    // Swap the threshold to a sensible default for the new metric so the
    // user doesn't have to clear the prefilled price when picking
    // sentiment (and vice-versa).
    if (m === "sentiment") {
      setThreshold("-0.3");
      setDirection("below");
    } else {
      setThreshold(lastPrice !== null ? lastPrice.toFixed(2) : "");
      setDirection("above");
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const canSubmit = (() => {
    if (submitting) return false;
    if (threshold.trim() === "") return false;
    const n = Number(threshold);
    if (!Number.isFinite(n)) return false;
    if (metric === "price") return n > 0;
    // sentiment: threshold must lie in [-1, +1]
    return n >= -1 && n <= 1;
  })();

  const submit = () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    const trimmedNote = note.trim();
    createAlert({
      asset_id: assetId,
      threshold: threshold.trim(),
      direction,
      metric,
      window_days: metric === "sentiment" ? windowDays : null,
      note: trimmedNote === "" ? null : trimmedNote,
    })
      .then((a) => {
        onCreated(a);
        onClose();
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        setSubmitting(false);
      });
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
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              <Bell className="h-4 w-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
                New alert for {symbol}
              </h2>
              <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                {assetName}
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
              htmlFor="alert-metric"
              className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
            >
              Metric
            </label>
            <div className="mt-1.5 grid grid-cols-2 gap-2">
              {(["price", "sentiment"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => onMetricChange(m)}
                  className={[
                    "rounded-md border px-3 py-2 text-sm font-medium capitalize transition-colors",
                    metric === m
                      ? "border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:border-emerald-500 dark:text-emerald-300"
                      : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800",
                  ].join(" ")}
                >
                  {m}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
              {metric === "price"
                ? "Fires when the latest close crosses your threshold."
                : "Fires when the rolling-mean compound sentiment crosses your threshold."}
            </p>
          </div>

          <div>
            <label
              htmlFor="alert-direction"
              className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
            >
              Direction
            </label>
            <div className="mt-1.5 grid grid-cols-2 gap-2">
              {(["above", "below"] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setDirection(d)}
                  className={[
                    "rounded-md border px-3 py-2 text-sm font-medium capitalize transition-colors",
                    direction === d
                      ? "border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:border-emerald-500 dark:text-emerald-300"
                      : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800",
                  ].join(" ")}
                >
                  {d === "above" ? "Rises above ↑" : "Drops below ↓"}
                </button>
              ))}
            </div>
          </div>

          {metric === "sentiment" && (
            <div>
              <label
                htmlFor="alert-window"
                className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
              >
                Rolling window
              </label>
              <div className="mt-1.5 grid grid-cols-3 gap-2">
                {SENTIMENT_WINDOW_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setWindowDays(opt.value)}
                    className={[
                      "rounded-md border px-3 py-2 text-sm font-medium transition-colors",
                      windowDays === opt.value
                        ? "border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:border-emerald-500 dark:text-emerald-300"
                        : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800",
                    ].join(" ")}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div>
            <label
              htmlFor="alert-threshold"
              className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
            >
              {metric === "price" ? "Threshold price" : "Threshold (compound)"}
            </label>
            <input
              id="alert-threshold"
              type="number"
              inputMode="decimal"
              step="any"
              min={metric === "price" ? "0" : "-1"}
              max={metric === "sentiment" ? "1" : undefined}
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              className="mt-1.5 block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 font-mono text-sm tabular-nums text-zinc-900 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              placeholder={metric === "price" ? "e.g. 150.00" : "e.g. -0.30"}
            />
            {metric === "price" ? (
              lastPrice !== null && (
                <p className="mt-1 text-[11px] text-zinc-400 dark:text-zinc-500">
                  Last close: {lastPrice.toFixed(2)}
                </p>
              )
            ) : (
              <p className="mt-1 text-[11px] text-zinc-400 dark:text-zinc-500">
                Range: -1 (very negative) to +1 (very positive). VADER's
                conventional thresholds are ±0.05 / ±0.30.
              </p>
            )}
          </div>

          <div>
            <label
              htmlFor="alert-note"
              className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
            >
              Note <span className="font-normal normal-case text-zinc-400 dark:text-zinc-500">(optional)</span>
            </label>
            <input
              id="alert-note"
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={256}
              className="mt-1.5 block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              placeholder="earnings watch, resistance level…"
            />
          </div>

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
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50 disabled:hover:bg-emerald-600"
          >
            {submitting ? "Creating…" : "Create alert"}
          </button>
        </div>
      </div>
    </div>
  );
}
