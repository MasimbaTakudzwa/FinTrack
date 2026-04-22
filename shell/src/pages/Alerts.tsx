/**
 * Alerts page — full list with history (triggered vs. armed), filter chip,
 * inline toggle/reset/delete.
 *
 * Pending alerts (triggered but not yet notified) are not highlighted here
 * since the global poller fires notifications every 30s — by the time the user
 * opens this page, they'll usually be ``notified_at`` already. Users who just
 * want the "what fired recently" view can sort by triggered_at.
 */

import { Bell, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  type PriceAlert,
  deleteAlert,
  listAlerts,
  updateAlert,
} from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";

type Filter = "all" | "active" | "triggered";

interface State {
  alerts: PriceAlert[];
  loading: boolean;
  error: string | null;
}

const INITIAL: State = { alerts: [], loading: true, error: null };

function fmtThreshold(a: PriceAlert): string {
  return Number(a.threshold).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  });
}

function fmtLastPrice(a: PriceAlert): string {
  if (a.last_price === null) return "—";
  return Number(a.last_price).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  });
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  // Backend serialises SQLite datetimes as naive; coerce to UTC for parsing.
  const normalised = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(normalised);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function Alerts() {
  const [state, setState] = useState<State>(INITIAL);
  const [filter, setFilter] = useState<Filter>("all");
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());
  const [pendingDelete, setPendingDelete] = useState<PriceAlert | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    listAlerts({ signal: controller.signal })
      .then((data) => {
        if (!cancelled) {
          setState({ alerts: data.alerts, loading: false, error: null });
        }
      })
      .catch((err: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setState({
          alerts: [],
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    listAlerts()
      .then((data) => {
        setState({ alerts: data.alerts, loading: false, error: null });
      })
      .catch((err: unknown) => {
        setState({
          alerts: [],
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      });
  };

  const markBusy = (id: number, busy: boolean) => {
    setBusyIds((prev) => {
      const next = new Set(prev);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const toggleActive = (a: PriceAlert) => {
    markBusy(a.id, true);
    updateAlert(a.id, { is_active: !a.is_active })
      .then((updated) => {
        setState((s) => ({
          ...s,
          alerts: s.alerts.map((x) => (x.id === a.id ? updated : x)),
        }));
      })
      .catch((err: unknown) => {
        console.error("toggle alert failed:", err);
      })
      .finally(() => markBusy(a.id, false));
  };

  const resetAlert = (a: PriceAlert) => {
    markBusy(a.id, true);
    updateAlert(a.id, { reset: true })
      .then((updated) => {
        setState((s) => ({
          ...s,
          alerts: s.alerts.map((x) => (x.id === a.id ? updated : x)),
        }));
      })
      .catch((err: unknown) => {
        console.error("reset alert failed:", err);
      })
      .finally(() => markBusy(a.id, false));
  };

  const requestDelete = (a: PriceAlert) => {
    setPendingDelete(a);
  };

  const confirmDelete = () => {
    const a = pendingDelete;
    if (!a) return;
    setPendingDelete(null);
    markBusy(a.id, true);
    deleteAlert(a.id)
      .then(() => {
        setState((s) => ({
          ...s,
          alerts: s.alerts.filter((x) => x.id !== a.id),
        }));
      })
      .catch((err: unknown) => {
        setState((s) => ({
          ...s,
          error: err instanceof Error ? err.message : String(err),
        }));
      })
      .finally(() => markBusy(a.id, false));
  };

  const filtered = state.alerts.filter((a) => {
    if (filter === "active") return a.is_active && !a.triggered_at;
    if (filter === "triggered") return a.triggered_at !== null;
    return true;
  });

  const counts = {
    all: state.alerts.length,
    active: state.alerts.filter((a) => a.is_active && !a.triggered_at).length,
    triggered: state.alerts.filter((a) => a.triggered_at !== null).length,
  };

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            Alerts
          </h2>
          <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
            {state.alerts.length} total — create alerts from any asset page.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border border-zinc-200 bg-white p-0.5 dark:border-zinc-700 dark:bg-zinc-900">
            {(["all", "active", "triggered"] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={[
                  "rounded px-3 py-1 text-xs font-medium capitalize transition-colors",
                  filter === f
                    ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100",
                ].join(" ")}
              >
                {f} <span className="opacity-60">({counts[f]})</span>
              </button>
            ))}
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
      </div>

      {state.error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          Failed to load alerts: {state.error}
        </div>
      )}

      {!state.error && state.loading && state.alerts.length === 0 && (
        <div className="rounded-md border border-zinc-200 bg-white p-8 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
          Loading…
        </div>
      )}

      {!state.error && !state.loading && state.alerts.length === 0 && (
        <div className="rounded-md border border-dashed border-zinc-300 bg-zinc-50 p-8 text-center dark:border-zinc-700 dark:bg-zinc-900/60">
          <Bell className="mx-auto h-8 w-8 text-zinc-400 dark:text-zinc-600" />
          <p className="mt-2 text-sm font-medium text-zinc-700 dark:text-zinc-300">
            No alerts yet
          </p>
          <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-500">
            Open any asset to create a price alert.
          </p>
        </div>
      )}

      {filtered.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
          <table className="w-full divide-y divide-zinc-200 text-sm dark:divide-zinc-800">
            <thead className="bg-zinc-50 text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
              <tr>
                <th className="px-4 py-2 text-left">Asset</th>
                <th className="px-4 py-2 text-right">Threshold</th>
                <th className="px-4 py-2 text-right">Last price</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-left">Triggered</th>
                <th className="px-4 py-2 text-left">Note</th>
                <th className="px-4 py-2 text-right" />
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {filtered.map((a) => {
                const busy = busyIds.has(a.id);
                const direction =
                  a.direction === "above" ? "↑ above" : "↓ below";
                return (
                  <tr
                    key={a.id}
                    className={
                      a.triggered_at
                        ? "bg-amber-50/60 dark:bg-amber-950/20"
                        : undefined
                    }
                  >
                    <td className="px-4 py-3">
                      <Link
                        to={`/assets/${encodeURIComponent(a.symbol)}`}
                        className="font-mono text-sm font-semibold text-zinc-900 hover:text-emerald-700 dark:text-zinc-100 dark:hover:text-emerald-400"
                      >
                        {a.symbol}
                      </Link>
                      <div className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                        {a.asset_name}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-800 dark:text-zinc-200">
                      <span className="text-[11px] uppercase text-zinc-400 dark:text-zinc-500">
                        {direction}
                      </span>{" "}
                      {fmtThreshold(a)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-zinc-600 dark:text-zinc-400">
                      {fmtLastPrice(a)}
                    </td>
                    <td className="px-4 py-3">
                      {a.triggered_at ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                          Triggered
                        </span>
                      ) : a.is_active ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
                          Armed
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                          Paused
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-[11px] text-zinc-500 dark:text-zinc-400">
                      {fmtDateTime(a.triggered_at)}
                    </td>
                    <td className="px-4 py-3 text-[12px] text-zinc-600 dark:text-zinc-300">
                      {a.note ?? <span className="text-zinc-300 dark:text-zinc-600">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => toggleActive(a)}
                          disabled={busy}
                          title={a.is_active ? "Pause" : "Resume"}
                          className="rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
                        >
                          {a.is_active ? "Pause" : "Resume"}
                        </button>
                        {a.triggered_at && (
                          <button
                            type="button"
                            onClick={() => resetAlert(a)}
                            disabled={busy}
                            title="Re-arm"
                            className="inline-flex items-center gap-1 rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
                          >
                            <RotateCcw className="h-3 w-3" />
                            Reset
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => requestDelete(a)}
                          disabled={busy}
                          title="Delete"
                          className="rounded border border-rose-200 bg-white p-1 text-rose-500 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-900 dark:bg-zinc-900 dark:text-rose-400 dark:hover:bg-rose-950"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {state.alerts.length > 0 && filtered.length === 0 && (
        <div className="rounded-md border border-dashed border-zinc-300 bg-zinc-50 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-400">
          No alerts match the <span className="font-medium">{filter}</span> filter.
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete alert?"
        message={
          pendingDelete
            ? `Delete the ${pendingDelete.direction} ${fmtThreshold(pendingDelete)} alert on ${pendingDelete.symbol}? This cannot be undone.`
            : ""
        }
        confirmLabel="Delete"
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
