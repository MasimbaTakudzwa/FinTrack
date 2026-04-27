import { useEffect, useRef, useState } from "react";
import { CheckCircle, FileUp, Upload, X } from "lucide-react";
import {
  importPortfolioTransactionsCsv,
  type ImportResult,
} from "../api/client";

interface Props {
  onClose: () => void;
  /** Called after a successful (partial or total) import so the parent
   *  can refresh its position/transaction lists. */
  onImported: (result: ImportResult) => void;
}

type State =
  | { status: "idle"; csv: string; result: null; error: null }
  | { status: "submitting"; csv: string; result: null; error: null }
  | { status: "done"; csv: string; result: ImportResult; error: null }
  | { status: "error"; csv: string; result: null; error: string };

const INITIAL: State = {
  status: "idle",
  csv: "",
  result: null,
  error: null,
};

const MAX_BYTES = 1_000_000; // 1 MB — way more than any reasonable single-user export

/**
 * Bulk-import portfolio transactions from a CSV file or pasted text.
 * Accepts either a file picker (typical for users restoring an export)
 * or a paste-into-textarea path (for users copying directly from a
 * spreadsheet). Both feed the same JSON-body endpoint so the UI is
 * just a thin renderer over `importPortfolioTransactionsCsv`.
 *
 * Result panel shows inserted/skipped counts plus per-row errors —
 * partial-success is the expected outcome for human-curated CSVs.
 */
export function TransactionImportModal({ onClose, onImported }: Props) {
  const [state, setState] = useState<State>(INITIAL);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const onFile = (file: File | null) => {
    if (!file) return;
    if (file.size > MAX_BYTES) {
      setState({
        status: "error",
        csv: "",
        result: null,
        error: `File too large (${(file.size / 1024).toFixed(0)} KB). Max ${MAX_BYTES / 1000} KB.`,
      });
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      // Move from idle → idle-with-CSV via the file-picker callback —
      // not an effect, so the React 19 set-state-in-effect rule is fine.
      setState({ status: "idle", csv: text, result: null, error: null });
    };
    reader.onerror = () => {
      setState({
        status: "error",
        csv: "",
        result: null,
        error: "Couldn't read the file.",
      });
    };
    reader.readAsText(file);
  };

  const submit = () => {
    if (state.csv.trim() === "") return;
    setState((s) =>
      s.status === "idle" || s.status === "error"
        ? { status: "submitting", csv: s.csv, result: null, error: null }
        : s,
    );
    importPortfolioTransactionsCsv(state.csv)
      .then((result) => {
        setState({
          status: "done",
          csv: state.csv,
          result,
          error: null,
        });
        if (result.inserted > 0) onImported(result);
      })
      .catch((err: unknown) => {
        setState({
          status: "error",
          csv: state.csv,
          result: null,
          error: err instanceof Error ? err.message : String(err),
        });
      });
  };

  const lineCount = state.csv ? state.csv.trim().split(/\r?\n/).length : 0;
  const previewLines = state.csv
    ? state.csv.split(/\r?\n/).slice(0, 6)
    : [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-start justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
              <Upload className="h-4 w-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
                Import transactions from CSV
              </h2>
              <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                Round-trip compatible with the export — pick a file or
                paste CSV text below.
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
          <div className="flex items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => onFile(e.target.files?.[0] ?? null)}
              className="hidden"
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <FileUp className="h-3.5 w-3.5" />
              Choose .csv file
            </button>
            {state.csv && (
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                {lineCount} line{lineCount === 1 ? "" : "s"} loaded
              </span>
            )}
          </div>

          <label className="block">
            <span className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Or paste CSV
            </span>
            <textarea
              value={state.csv}
              onChange={(e) =>
                setState({
                  status: "idle",
                  csv: e.target.value,
                  result: null,
                  error: null,
                })
              }
              rows={6}
              className="mt-1.5 block w-full rounded-md border border-zinc-200 bg-white px-3 py-2 font-mono text-[11px] text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              placeholder={
                "transaction_date,symbol,transaction_type,quantity,price_per_unit,fee,notes\n" +
                "2026-04-01,AAPL,buy,10,150.00,0,…"
              }
            />
          </label>

          {previewLines.length > 0 && state.status === "idle" && (
            <div className="rounded-md border border-zinc-200 bg-zinc-50 p-2 text-[11px] text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
              <div className="mb-1 font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Preview (first 6 lines)
              </div>
              {previewLines.map((line, i) => (
                <div key={i} className="font-mono break-all">
                  {line || <span className="text-zinc-400">(blank)</span>}
                </div>
              ))}
              {lineCount > 6 && (
                <div className="mt-1 italic text-zinc-500 dark:text-zinc-400">
                  + {lineCount - 6} more line{lineCount - 6 === 1 ? "" : "s"}…
                </div>
              )}
            </div>
          )}

          {state.status === "done" && state.result && (
            <ResultPanel result={state.result} />
          )}

          {state.status === "error" && (
            <div className="rounded-md border border-rose-200 bg-rose-50 p-2 text-xs text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
              {state.error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-zinc-200 px-5 py-3 dark:border-zinc-800">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            {state.status === "done" ? "Close" : "Cancel"}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={state.csv.trim() === "" || state.status === "submitting"}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {state.status === "submitting" ? "Importing…" : "Import"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ResultPanel({ result }: { result: ImportResult }) {
  const success = result.inserted > 0 && result.skipped === 0;
  return (
    <div
      className={`rounded-md border p-3 text-xs ${
        success
          ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300"
          : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300"
      }`}
    >
      <div className="mb-1 inline-flex items-center gap-1.5 font-semibold">
        {success && <CheckCircle className="h-3.5 w-3.5" />}
        Imported {result.inserted} transaction
        {result.inserted === 1 ? "" : "s"}
        {result.skipped > 0 && (
          <span> · {result.skipped} skipped</span>
        )}
      </div>
      {result.errors.length > 0 && (
        <ul className="mt-1.5 max-h-40 space-y-0.5 overflow-auto text-[11px]">
          {result.errors.map((e, i) => (
            <li key={i}>
              <span className="font-mono">row {e.row}:</span> {e.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
