import { useEffect, useMemo, useState } from "react";
import { Briefcase, X } from "lucide-react";
import {
  type Asset,
  type TransactionType,
  createPortfolioTransaction,
} from "../api/client";

interface Props {
  assets: Asset[];
  /** Optional default asset (e.g. "Buy AAPL" deep-link from AssetDetail). */
  defaultAssetId?: number;
  onClose: () => void;
  onCreated: () => void;
}

/**
 * Compact form for entering a buy/sell transaction. Drives the
 * portfolio-page entry path. The asset picker uses the existing
 * `assets` catalog passed in by the parent (no extra fetch).
 *
 * Validation is light on the client side — service layer is the source
 * of truth for "quantity must be > 0", "asset must exist", etc. — so
 * the modal just disables the submit button when fields are obviously
 * empty and trusts the API to surface real errors.
 */
export function TransactionAddModal({
  assets,
  defaultAssetId,
  onClose,
  onCreated,
}: Props) {
  const sortedAssets = useMemo(
    () => [...assets].sort((a, b) => a.symbol.localeCompare(b.symbol)),
    [assets],
  );
  const [assetId, setAssetId] = useState<number | "">(
    defaultAssetId ?? sortedAssets[0]?.id ?? "",
  );
  const [type, setType] = useState<TransactionType>("buy");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [fee, setFee] = useState("0");
  const [date, setDate] = useState(today());
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const canSubmit =
    !submitting &&
    assetId !== "" &&
    quantity.trim() !== "" &&
    Number(quantity) > 0 &&
    price.trim() !== "" &&
    Number(price) > 0 &&
    date.trim() !== "";

  const submit = () => {
    if (!canSubmit) return;
    if (typeof assetId !== "number") return;
    setSubmitting(true);
    setError(null);
    const trimmedNotes = notes.trim();
    createPortfolioTransaction({
      asset_id: assetId,
      transaction_type: type,
      quantity: quantity.trim(),
      price_per_unit: price.trim(),
      transaction_date: date,
      fee: fee.trim() || "0",
      notes: trimmedNotes === "" ? null : trimmedNotes,
    })
      .then(() => onCreated())
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
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
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-start justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              <Briefcase className="h-4 w-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
                Record transaction
              </h2>
              <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                Buy or sell to update your portfolio P&amp;L.
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

        <div className="space-y-3 px-5 py-4">
          <div className="grid grid-cols-2 gap-3">
            {(["buy", "sell"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setType(t)}
                className={[
                  "rounded-md border px-3 py-2 text-sm font-medium capitalize transition-colors",
                  type === t
                    ? t === "buy"
                      ? "border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:border-emerald-500 dark:text-emerald-300"
                      : "border-rose-500 bg-rose-500/10 text-rose-700 dark:border-rose-500 dark:text-rose-300"
                    : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800",
                ].join(" ")}
              >
                {t}
              </button>
            ))}
          </div>

          <Field label="Asset">
            <select
              value={assetId}
              onChange={(e) =>
                setAssetId(e.target.value === "" ? "" : Number(e.target.value))
              }
              className="block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
            >
              {sortedAssets.length === 0 && (
                <option value="">No assets tracked</option>
              )}
              {sortedAssets.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.symbol} — {a.name}
                </option>
              ))}
            </select>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Quantity">
              <input
                type="number"
                inputMode="decimal"
                step="any"
                min="0"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 font-mono text-sm tabular-nums text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                placeholder="10"
              />
            </Field>
            <Field label="Price per unit">
              <input
                type="number"
                inputMode="decimal"
                step="any"
                min="0"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                className="block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 font-mono text-sm tabular-nums text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                placeholder="150.00"
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Date">
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className="block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 font-mono text-sm tabular-nums text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              />
            </Field>
            <Field label="Fee (optional)">
              <input
                type="number"
                inputMode="decimal"
                step="any"
                min="0"
                value={fee}
                onChange={(e) => setFee(e.target.value)}
                className="block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 font-mono text-sm tabular-nums text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                placeholder="0"
              />
            </Field>
          </div>

          <Field label="Notes (optional)">
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={256}
              className="block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              placeholder="brokerage, strategy, …"
            />
          </Field>

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
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </span>
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

function today(): string {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}
