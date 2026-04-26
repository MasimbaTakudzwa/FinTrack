import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDownRight,
  ArrowUpRight,
  Briefcase,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import {
  type Asset,
  type PortfolioPosition,
  type PortfolioSummary,
  type PortfolioTransaction,
  deletePortfolioTransaction,
  getPortfolioSummary,
  listAssets,
  listPortfolioPositions,
  listPortfolioTransactions,
} from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { TransactionAddModal } from "../components/TransactionAddModal";

type TabId = "positions" | "transactions";

interface State {
  positions: PortfolioPosition[];
  transactions: PortfolioTransaction[];
  summary: PortfolioSummary | null;
  assets: Asset[];
  loading: boolean;
  error: string | null;
}

const INITIAL: State = {
  positions: [],
  transactions: [],
  summary: null,
  assets: [],
  loading: true,
  error: null,
};

/**
 * Portfolio dashboard. Top: rollup card with cost basis / current value
 * / unrealized + realized P&L. Below: tabs for Positions (current state)
 * and Transactions (raw log). Add-transaction modal opens from the
 * header button. Average-cost basis is computed on the backend so the
 * UI is essentially a renderer.
 */
export function Portfolio() {
  const [state, setState] = useState<State>(INITIAL);
  const [tab, setTab] = useState<TabId>("positions");
  const [tick, setTick] = useState(0);
  const [addOpen, setAddOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<PortfolioTransaction | null>(
    null,
  );

  useEffect(() => {
    const ac = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const [positions, transactions, summary, assets] = await Promise.all([
          listPortfolioPositions(ac.signal),
          listPortfolioTransactions({ signal: ac.signal }),
          getPortfolioSummary(ac.signal),
          listAssets({ activeOnly: false, signal: ac.signal }),
        ]);
        if (cancelled) return;
        setState({
          positions: positions.positions,
          transactions: transactions.transactions,
          summary,
          assets,
          loading: false,
          error: null,
        });
      } catch (err) {
        if (cancelled || ac.signal.aborted) return;
        setState((prev) => ({
          ...prev,
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        }));
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [tick]);

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    setTick((t) => t + 1);
  };

  const onTransactionAdded = () => {
    setAddOpen(false);
    refresh();
  };

  const onConfirmDelete = async () => {
    if (!pendingDelete) return;
    const id = pendingDelete.id;
    setPendingDelete(null);
    try {
      await deletePortfolioTransaction(id);
      refresh();
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : String(err),
      }));
    }
  };

  return (
    <div className="p-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            <Briefcase className="h-5 w-5 text-zinc-400" />
            Portfolio
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Average-cost basis · unrealized P&amp;L vs latest close · all data local.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={refresh}
            disabled={state.loading}
            className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${state.loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            disabled={state.assets.length === 0}
            className="inline-flex items-center gap-2 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            Add transaction
          </button>
        </div>
      </div>

      {state.error && (
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          {state.error}
        </div>
      )}

      <SummaryCard summary={state.summary} loading={state.loading} />

      <div className="mt-5 mb-3 flex gap-2 border-b border-zinc-200 dark:border-zinc-800">
        {(["positions", "transactions"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={[
              "border-b-2 px-3 py-2 text-sm font-medium capitalize -mb-px",
              tab === t
                ? "border-emerald-500 text-emerald-700 dark:text-emerald-400"
                : "border-transparent text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200",
            ].join(" ")}
          >
            {t}
            {t === "positions" && state.positions.length > 0 && (
              <span className="ml-1.5 rounded-full bg-zinc-100 px-1.5 py-0.5 text-[10px] dark:bg-zinc-800">
                {state.positions.length}
              </span>
            )}
            {t === "transactions" && state.transactions.length > 0 && (
              <span className="ml-1.5 rounded-full bg-zinc-100 px-1.5 py-0.5 text-[10px] dark:bg-zinc-800">
                {state.transactions.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === "positions" ? (
        <PositionsTable
          positions={state.positions}
          loading={state.loading}
          onAdd={() => setAddOpen(true)}
          assetsAvailable={state.assets.length > 0}
        />
      ) : (
        <TransactionsTable
          transactions={state.transactions}
          loading={state.loading}
          onDelete={(t) => setPendingDelete(t)}
        />
      )}

      {addOpen && (
        <TransactionAddModal
          assets={state.assets}
          onClose={() => setAddOpen(false)}
          onCreated={onTransactionAdded}
        />
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete transaction?"
        message={
          pendingDelete
            ? `Delete the ${pendingDelete.transaction_type} of ${pendingDelete.quantity} ${pendingDelete.symbol} on ${pendingDelete.transaction_date}? Position state is recomputed from the remaining transactions.`
            : ""
        }
        confirmLabel="Delete"
        destructive
        onCancel={() => setPendingDelete(null)}
        onConfirm={onConfirmDelete}
      />
    </div>
  );
}

function SummaryCard({
  summary,
  loading,
}: {
  summary: PortfolioSummary | null;
  loading: boolean;
}) {
  if (!summary && loading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white p-4 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading portfolio…
      </div>
    );
  }
  if (!summary) return null;

  const unreal = Number(summary.total_unrealized_pl);
  const real = Number(summary.total_realized_pl);
  const totalPl = unreal + real;
  const tone = (n: number) =>
    n > 0
      ? "text-emerald-600 dark:text-emerald-400"
      : n < 0
        ? "text-rose-600 dark:text-rose-400"
        : "text-zinc-700 dark:text-zinc-300";
  const fmt = (n: number) =>
    `${n > 0 ? "+" : ""}${Math.abs(n).toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).replace(/^-/, "-")}`;

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Current value
          </div>
          <div className="mt-1 text-3xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
            {Number(summary.total_current_value).toLocaleString(undefined, {
              style: "currency",
              currency: "USD",
              maximumFractionDigits: 2,
            })}
          </div>
          <div className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
            {summary.open_positions} open position
            {summary.open_positions === 1 ? "" : "s"}
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Cost basis
            </dt>
            <dd className="font-mono tabular-nums text-zinc-700 dark:text-zinc-300">
              {Number(summary.total_cost_basis).toLocaleString(undefined, {
                style: "currency",
                currency: "USD",
                maximumFractionDigits: 2,
              })}
            </dd>
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Unrealized
            </dt>
            <dd className={`font-mono tabular-nums ${tone(unreal)}`}>
              {fmt(unreal)}
            </dd>
            {summary.total_unrealized_pl_pct !== null && (
              <dd className={`text-[11px] ${tone(unreal)}`}>
                {Number(summary.total_unrealized_pl_pct) > 0 ? "+" : ""}
                {Number(summary.total_unrealized_pl_pct).toFixed(2)}%
              </dd>
            )}
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Realized
            </dt>
            <dd className={`font-mono tabular-nums ${tone(real)}`}>
              {fmt(real)}
            </dd>
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Total P&amp;L
            </dt>
            <dd className={`font-mono tabular-nums font-semibold ${tone(totalPl)}`}>
              {fmt(totalPl)}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

function PositionsTable({
  positions,
  loading,
  onAdd,
  assetsAvailable,
}: {
  positions: PortfolioPosition[];
  loading: boolean;
  onAdd: () => void;
  assetsAvailable: boolean;
}) {
  if (loading && positions.length === 0) {
    return (
      <div className="rounded-md border border-zinc-200 bg-white p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
        <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
        Loading positions…
      </div>
    );
  }
  if (positions.length === 0) {
    return (
      <div className="rounded-md border border-zinc-200 bg-white p-6 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
        <p>No transactions yet — add a buy to start tracking your portfolio.</p>
        {assetsAvailable && (
          <button
            type="button"
            onClick={onAdd}
            className="mt-3 inline-flex items-center gap-2 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
          >
            <Plus className="h-3.5 w-3.5" />
            Add transaction
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
      <table className="w-full text-left text-sm">
        <thead className="bg-zinc-50 text-[10px] uppercase tracking-wide text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
          <tr>
            <th className="px-4 py-2 font-medium">Asset</th>
            <th className="px-4 py-2 text-right font-medium">Qty</th>
            <th className="px-4 py-2 text-right font-medium">Avg cost</th>
            <th className="px-4 py-2 text-right font-medium">Last close</th>
            <th className="px-4 py-2 text-right font-medium">Cost basis</th>
            <th className="px-4 py-2 text-right font-medium">Value</th>
            <th className="px-4 py-2 text-right font-medium">Unrealized</th>
            <th className="px-4 py-2 text-right font-medium">Realized</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {positions.map((p) => (
            <PositionRow key={p.asset_id} p={p} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PositionRow({ p }: { p: PortfolioPosition }) {
  const qty = Number(p.quantity);
  const closed = qty === 0;
  const unreal = p.unrealized_pl !== null ? Number(p.unrealized_pl) : null;
  const real = Number(p.realized_pl);
  const tone = (n: number | null) =>
    n === null
      ? "text-zinc-400"
      : n > 0
        ? "text-emerald-600 dark:text-emerald-400"
        : n < 0
          ? "text-rose-600 dark:text-rose-400"
          : "text-zinc-700 dark:text-zinc-300";
  const fmt$ = (n: number | string | null) =>
    n === null
      ? "—"
      : Number(n).toLocaleString(undefined, {
          style: "currency",
          currency: "USD",
          maximumFractionDigits: 2,
        });
  const ArrowIcon =
    unreal === null ? null : unreal > 0 ? ArrowUpRight : ArrowDownRight;

  return (
    <tr
      className={
        closed ? "bg-zinc-50/50 text-zinc-500 dark:bg-zinc-900/50 dark:text-zinc-500" : undefined
      }
    >
      <td className="px-4 py-3">
        <Link
          to={`/assets/${encodeURIComponent(p.symbol)}`}
          className="font-mono font-semibold text-zinc-900 hover:text-emerald-700 dark:text-zinc-100 dark:hover:text-emerald-400"
        >
          {p.symbol}
        </Link>
        <div className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
          {p.asset_name}
          {closed && (
            <span className="ml-2 rounded-full bg-zinc-100 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
              Closed
            </span>
          )}
        </div>
      </td>
      <td className="px-4 py-3 text-right font-mono tabular-nums">{p.quantity}</td>
      <td className="px-4 py-3 text-right font-mono tabular-nums">
        {fmt$(p.avg_cost)}
      </td>
      <td className="px-4 py-3 text-right font-mono tabular-nums">
        {fmt$(p.last_close)}
      </td>
      <td className="px-4 py-3 text-right font-mono tabular-nums">
        {fmt$(p.cost_basis)}
      </td>
      <td className="px-4 py-3 text-right font-mono tabular-nums">
        {fmt$(p.current_value)}
      </td>
      <td className={`px-4 py-3 text-right font-mono tabular-nums ${tone(unreal)}`}>
        {ArrowIcon && unreal !== null && (
          <ArrowIcon className="mr-1 inline h-3 w-3" />
        )}
        {fmt$(p.unrealized_pl)}
        {p.unrealized_pl_pct !== null && (
          <div className="text-[10px]">
            {Number(p.unrealized_pl_pct) > 0 ? "+" : ""}
            {Number(p.unrealized_pl_pct).toFixed(2)}%
          </div>
        )}
      </td>
      <td className={`px-4 py-3 text-right font-mono tabular-nums ${tone(real)}`}>
        {fmt$(p.realized_pl)}
      </td>
    </tr>
  );
}

function TransactionsTable({
  transactions,
  loading,
  onDelete,
}: {
  transactions: PortfolioTransaction[];
  loading: boolean;
  onDelete: (t: PortfolioTransaction) => void;
}) {
  if (loading && transactions.length === 0) {
    return (
      <div className="rounded-md border border-zinc-200 bg-white p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
        <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
        Loading transactions…
      </div>
    );
  }
  if (transactions.length === 0) {
    return (
      <div className="rounded-md border border-zinc-200 bg-white p-6 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
        No transactions yet.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
      <table className="w-full text-left text-sm">
        <thead className="bg-zinc-50 text-[10px] uppercase tracking-wide text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
          <tr>
            <th className="px-4 py-2 font-medium">Date</th>
            <th className="px-4 py-2 font-medium">Asset</th>
            <th className="px-4 py-2 font-medium">Type</th>
            <th className="px-4 py-2 text-right font-medium">Quantity</th>
            <th className="px-4 py-2 text-right font-medium">Price</th>
            <th className="px-4 py-2 text-right font-medium">Fee</th>
            <th className="px-4 py-2 text-right font-medium">Total</th>
            <th className="px-4 py-2 font-medium">Notes</th>
            <th className="px-4 py-2 text-right font-medium" />
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {transactions.map((t) => {
            const total =
              t.transaction_type === "buy"
                ? Number(t.quantity) * Number(t.price_per_unit) + Number(t.fee)
                : Number(t.quantity) * Number(t.price_per_unit) - Number(t.fee);
            const isBuy = t.transaction_type === "buy";
            return (
              <tr key={t.id}>
                <td className="px-4 py-2 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                  {t.transaction_date}
                </td>
                <td className="px-4 py-2">
                  <Link
                    to={`/assets/${encodeURIComponent(t.symbol)}`}
                    className="font-mono font-semibold text-zinc-900 hover:text-emerald-700 dark:text-zinc-100 dark:hover:text-emerald-400"
                  >
                    {t.symbol}
                  </Link>
                </td>
                <td className="px-4 py-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                      isBuy
                        ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
                        : "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300"
                    }`}
                  >
                    {t.transaction_type}
                  </span>
                </td>
                <td className="px-4 py-2 text-right font-mono tabular-nums">
                  {t.quantity}
                </td>
                <td className="px-4 py-2 text-right font-mono tabular-nums">
                  {Number(t.price_per_unit).toLocaleString(undefined, {
                    style: "currency",
                    currency: "USD",
                    maximumFractionDigits: 2,
                  })}
                </td>
                <td className="px-4 py-2 text-right font-mono tabular-nums text-zinc-500 dark:text-zinc-400">
                  {Number(t.fee) === 0
                    ? "—"
                    : Number(t.fee).toLocaleString(undefined, {
                        style: "currency",
                        currency: "USD",
                        maximumFractionDigits: 2,
                      })}
                </td>
                <td className="px-4 py-2 text-right font-mono tabular-nums">
                  {total.toLocaleString(undefined, {
                    style: "currency",
                    currency: "USD",
                    maximumFractionDigits: 2,
                  })}
                </td>
                <td className="px-4 py-2 max-w-xs truncate text-xs text-zinc-500 dark:text-zinc-400">
                  {t.notes ?? "—"}
                </td>
                <td className="px-4 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => onDelete(t)}
                    title="Delete transaction"
                    className="rounded p-1 text-zinc-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950 dark:hover:text-rose-400"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
