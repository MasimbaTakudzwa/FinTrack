import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  GripVertical,
  Pencil,
  Plus,
  Star,
  Trash2,
  X,
} from "lucide-react";
import {
  DndContext,
  type DragEndEvent,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  type Asset,
  type WatchlistDetail,
  type WatchlistItem,
  type WatchlistSummary,
  addWatchlistItem,
  createWatchlist,
  deleteWatchlist,
  getWatchlist,
  listAssets,
  listWatchlists,
  removeWatchlistItem,
  reorderWatchlistItems,
  updateWatchlist,
} from "../api/client";
import { AddAssetModal } from "../components/AddAssetModal";
import { ConfirmDialog } from "../components/ConfirmDialog";

interface State {
  watchlists: WatchlistSummary[];
  selectedId: number | null;
  detail: WatchlistDetail | null;
  assets: Asset[];
  loading: boolean;
  error: string | null;
}

const INITIAL: State = {
  watchlists: [],
  selectedId: null,
  detail: null,
  assets: [],
  loading: true,
  error: null,
};

export function Watchlists() {
  const [state, setState] = useState<State>(INITIAL);
  const [busy, setBusy] = useState(false);
  const [showNewForm, setShowNewForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [renameValue, setRenameValue] = useState<string | null>(null);
  // Separate from busy so the confirm dialog isn't blocked by a pending select.
  const [pendingDelete, setPendingDelete] = useState<WatchlistSummary | null>(
    null,
  );
  const [addAssetOpen, setAddAssetOpen] = useState(false);

  const refreshLists = useCallback(
    async (selectId?: number | null, signal?: AbortSignal) => {
      const [listResp, assets] = await Promise.all([
        listWatchlists(signal),
        listAssets({ activeOnly: false, signal }),
      ]);
      const watchlists = listResp.watchlists;
      const chosen =
        selectId && watchlists.some((w) => w.id === selectId)
          ? selectId
          : watchlists.find((w) => w.is_default)?.id ?? watchlists[0]?.id ?? null;
      let detail: WatchlistDetail | null = null;
      if (chosen !== null) {
        detail = await getWatchlist(chosen, signal);
      }
      setState({
        watchlists,
        selectedId: chosen,
        detail,
        assets,
        loading: false,
        error: null,
      });
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        await refreshLists(null, controller.signal);
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        setState((prev) => ({
          ...prev,
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        }));
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [refreshLists]);

  const selectList = async (id: number) => {
    if (id === state.selectedId) return;
    setBusy(true);
    try {
      const detail = await getWatchlist(id);
      setState((s) => ({ ...s, selectedId: id, detail, error: null }));
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setBusy(false);
    }
  };

  const onCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    try {
      const created = await createWatchlist({ name });
      setNewName("");
      setShowNewForm(false);
      await refreshLists(created.id);
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setBusy(false);
    }
  };

  const requestDelete = (wl: WatchlistSummary) => {
    if (wl.is_default) {
      setState((s) => ({
        ...s,
        error: "Cannot delete the default watchlist — promote another first.",
      }));
      return;
    }
    setPendingDelete(wl);
  };

  const confirmDelete = async () => {
    const target = pendingDelete;
    if (!target) return;
    setPendingDelete(null);
    setBusy(true);
    try {
      await deleteWatchlist(target.id);
      await refreshLists(null);
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setBusy(false);
    }
  };

  const onPromote = async (id: number) => {
    setBusy(true);
    try {
      await updateWatchlist(id, { is_default: true });
      await refreshLists(id);
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setBusy(false);
    }
  };

  const onRename = async (id: number) => {
    const name = renameValue?.trim() ?? "";
    if (!name) {
      setRenameValue(null);
      return;
    }
    setBusy(true);
    try {
      await updateWatchlist(id, { name });
      setRenameValue(null);
      await refreshLists(id);
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setBusy(false);
    }
  };

  const onAddAsset = async (assetId: number) => {
    if (state.selectedId === null) return;
    setBusy(true);
    try {
      await addWatchlistItem(state.selectedId, assetId);
      await refreshLists(state.selectedId);
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setBusy(false);
    }
  };

  const onRemoveAsset = async (assetId: number) => {
    if (state.selectedId === null) return;
    setBusy(true);
    try {
      await removeWatchlistItem(state.selectedId, assetId);
      await refreshLists(state.selectedId);
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setBusy(false);
    }
  };

  // Reorder uses optimistic UI: swap positions locally, then sync. On failure
  // the catch block refetches to get back to truth.
  const onReorder = async (newOrder: WatchlistItem[]) => {
    if (state.selectedId === null || state.detail === null) return;
    const prevItems = state.detail.items;
    setState((s) =>
      s.detail === null ? s : { ...s, detail: { ...s.detail, items: newOrder } },
    );
    try {
      await reorderWatchlistItems(
        state.selectedId,
        newOrder.map((i) => i.asset_id),
      );
    } catch (err) {
      // Revert on failure.
      setState((s) =>
        s.detail === null
          ? s
          : {
              ...s,
              detail: { ...s.detail, items: prevItems },
              error: err instanceof Error ? err.message : String(err),
            },
      );
    }
  };

  const availableAssets = useMemo(() => {
    const onList = new Set(state.detail?.items.map((i) => i.asset_id) ?? []);
    return state.assets
      .filter((a) => !onList.has(a.id))
      .sort((a, b) => a.symbol.localeCompare(b.symbol));
  }, [state.assets, state.detail]);

  return (
    <div className="flex h-full flex-col p-6">
      {state.error && (
        <div className="mb-4 flex items-start justify-between gap-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          <span>{state.error}</span>
          <button
            type="button"
            className="text-rose-600 hover:text-rose-800 dark:text-rose-300 dark:hover:text-rose-100"
            onClick={() => setState((s) => ({ ...s, error: null }))}
            aria-label="Dismiss"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        {/* Left: list of watchlists */}
        <aside className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Watchlists
            </h3>
            <button
              type="button"
              onClick={() => setShowNewForm((v) => !v)}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded-md border border-zinc-200 bg-white px-2 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <Plus className="h-3 w-3" />
              New
            </button>
          </div>

          {showNewForm && (
            <div className="flex items-center gap-2 rounded-md border border-zinc-200 bg-white p-2 dark:border-zinc-700 dark:bg-zinc-900">
              <input
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onCreate();
                  if (e.key === "Escape") {
                    setShowNewForm(false);
                    setNewName("");
                  }
                }}
                placeholder="Name…"
                className="min-w-0 flex-1 rounded-sm border border-zinc-200 bg-white px-2 py-1 text-xs text-zinc-900 focus:border-emerald-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              />
              <button
                type="button"
                onClick={onCreate}
                disabled={busy || !newName.trim()}
                className="rounded-sm bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                Add
              </button>
            </div>
          )}

          {state.loading ? (
            <div className="rounded-md border border-zinc-200 bg-white p-3 text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
              Loading…
            </div>
          ) : state.watchlists.length === 0 ? (
            <div className="rounded-md border border-dashed border-zinc-300 bg-zinc-50 p-3 text-xs text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-400">
              No watchlists yet.
            </div>
          ) : (
            <ul className="space-y-1">
              {state.watchlists.map((w) => (
                <li key={w.id}>
                  <WatchlistRow
                    wl={w}
                    selected={w.id === state.selectedId}
                    renaming={renameValue !== null && w.id === state.selectedId}
                    renameValue={renameValue ?? ""}
                    onRenameChange={setRenameValue}
                    onRenameCancel={() => setRenameValue(null)}
                    onRenameSubmit={() => onRename(w.id)}
                    onSelect={() => selectList(w.id)}
                    onPromote={() => onPromote(w.id)}
                    onStartRename={() => {
                      setRenameValue(w.name);
                      setState((s) => ({ ...s, selectedId: w.id }));
                    }}
                    onDelete={() => requestDelete(w)}
                    busy={busy}
                  />
                </li>
              ))}
            </ul>
          )}
        </aside>

        {/* Right: selected watchlist detail */}
        <main className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
          {state.loading ? (
            <div className="p-6 text-sm text-zinc-500 dark:text-zinc-400">
              Loading…
            </div>
          ) : state.detail === null ? (
            <div className="p-6 text-sm text-zinc-500 dark:text-zinc-400">
              Create a watchlist on the left to get started.
            </div>
          ) : (
            <WatchlistItems
              detail={state.detail}
              availableAssets={availableAssets}
              onReorder={onReorder}
              onAddAsset={onAddAsset}
              onRemoveAsset={onRemoveAsset}
              onTrackNew={() => setAddAssetOpen(true)}
              busy={busy}
            />
          )}
        </main>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete watchlist?"
        message={
          pendingDelete
            ? `Delete "${pendingDelete.name}"? Items on the list are unlinked but the underlying assets stay in your library.`
            : ""
        }
        confirmLabel="Delete"
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />

      {addAssetOpen && (
        <AddAssetModal
          onClose={() => setAddAssetOpen(false)}
          // The backend already adds to the default watchlist — which is
          // usually what you want. If the user is looking at a non-default
          // watchlist, skip the auto-add and rely on the dropdown flow.
          addToDefaultWatchlist={state.detail?.is_default ?? true}
          onCreated={(asset) => {
            void (async () => {
              // If the user is on a non-default watchlist, also link the new
              // asset here — that's the most intuitive outcome given the
              // button's visual context.
              if (state.selectedId !== null && !state.detail?.is_default) {
                try {
                  await addWatchlistItem(state.selectedId, asset.id);
                } catch {
                  // non-fatal — refresh below will surface discrepancies
                }
              }
              await refreshLists(state.selectedId);
            })();
          }}
        />
      )}
    </div>
  );
}

interface WatchlistRowProps {
  wl: WatchlistSummary;
  selected: boolean;
  renaming: boolean;
  renameValue: string;
  onRenameChange: (v: string) => void;
  onRenameCancel: () => void;
  onRenameSubmit: () => void;
  onSelect: () => void;
  onPromote: () => void;
  onStartRename: () => void;
  onDelete: () => void;
  busy: boolean;
}

function WatchlistRow({
  wl,
  selected,
  renaming,
  renameValue,
  onRenameChange,
  onRenameCancel,
  onRenameSubmit,
  onSelect,
  onPromote,
  onStartRename,
  onDelete,
  busy,
}: WatchlistRowProps) {
  const base = selected
    ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
    : "bg-white text-zinc-800 hover:bg-zinc-50 dark:bg-zinc-950 dark:text-zinc-200 dark:hover:bg-zinc-900";
  const iconCls = selected
    ? "text-white/70 hover:text-white dark:text-zinc-900/70 dark:hover:text-zinc-900"
    : "text-zinc-400 hover:text-zinc-700 dark:text-zinc-500 dark:hover:text-zinc-200";

  if (renaming) {
    return (
      <div
        className={`flex items-center gap-1 rounded-md border border-zinc-200 px-2 py-1.5 dark:border-zinc-700 ${base}`}
      >
        <input
          autoFocus
          value={renameValue}
          onChange={(e) => onRenameChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onRenameSubmit();
            if (e.key === "Escape") onRenameCancel();
          }}
          className="min-w-0 flex-1 rounded-sm bg-transparent px-1 py-0.5 text-sm focus:outline-none"
        />
        <button
          type="button"
          onClick={onRenameSubmit}
          aria-label="Save"
          className={iconCls}
        >
          <Check className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={onRenameCancel}
          aria-label="Cancel"
          className={iconCls}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div
      className={`group flex items-center gap-1 rounded-md px-2 py-1.5 ${base}`}
    >
      <button
        type="button"
        onClick={onSelect}
        disabled={busy}
        className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm font-medium"
      >
        {wl.is_default && <Star className="h-3.5 w-3.5 shrink-0 fill-current" />}
        <span className="truncate">{wl.name}</span>
        <span className="ml-auto shrink-0 rounded bg-zinc-200/50 px-1.5 py-0.5 text-[10px] font-medium text-zinc-600 dark:bg-zinc-700/50 dark:text-zinc-300">
          {wl.item_count}
        </span>
      </button>
      {/* Action strip is always rendered — hover-only used to hide the delete
          button entirely, which made the feature look broken. On the default
          watchlist the delete/promote buttons are disabled with a tooltip. */}
      <div className="flex">
        <button
          type="button"
          onClick={onPromote}
          disabled={busy || wl.is_default}
          aria-label={wl.is_default ? "Already default" : "Set as default"}
          title={wl.is_default ? "Already the default" : "Set as default"}
          className={`p-1 disabled:cursor-not-allowed disabled:opacity-40 ${iconCls}`}
        >
          <Star className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onStartRename}
          disabled={busy}
          aria-label="Rename"
          className={`p-1 ${iconCls}`}
          title="Rename"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onDelete}
          disabled={busy || wl.is_default}
          aria-label={wl.is_default ? "Cannot delete default" : "Delete"}
          title={
            wl.is_default
              ? "Cannot delete the default — promote another first"
              : "Delete"
          }
          className={`p-1 disabled:cursor-not-allowed disabled:opacity-40 ${iconCls}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

interface WatchlistItemsProps {
  detail: WatchlistDetail;
  availableAssets: Asset[];
  onReorder: (items: WatchlistItem[]) => void | Promise<void>;
  onAddAsset: (assetId: number) => void | Promise<void>;
  onRemoveAsset: (assetId: number) => void | Promise<void>;
  onTrackNew: () => void;
  busy: boolean;
}

function WatchlistItems({
  detail,
  availableAssets,
  onReorder,
  onAddAsset,
  onRemoveAsset,
  onTrackNew,
  busy,
}: WatchlistItemsProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );
  const [selectedAssetId, setSelectedAssetId] = useState<number | "">("");

  // If the underlying list changes (e.g. removed an item), reset the add-asset
  // dropdown so a stale id isn't submitted.
  const prevDetailKey = useRef<string>("");
  useEffect(() => {
    const key = detail.items.map((i) => i.asset_id).join(",");
    if (prevDetailKey.current !== key) {
      prevDetailKey.current = key;
      setSelectedAssetId("");
    }
  }, [detail]);

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const ids = detail.items.map((i) => i.asset_id);
    const from = ids.indexOf(Number(active.id));
    const to = ids.indexOf(Number(over.id));
    if (from === -1 || to === -1) return;
    const moved = arrayMove(detail.items, from, to);
    await onReorder(moved);
  };

  return (
    <>
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            {detail.is_default && (
              <Star className="h-4 w-4 fill-amber-400 text-amber-500" />
            )}
            {detail.name}
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {detail.items.length} item{detail.items.length === 1 ? "" : "s"}
            {detail.is_default ? " · powers the Dashboard" : ""}
          </p>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (typeof selectedAssetId === "number") {
              void onAddAsset(selectedAssetId);
            }
          }}
          className="flex items-center gap-2"
        >
          <select
            value={selectedAssetId}
            onChange={(e) =>
              setSelectedAssetId(e.target.value ? Number(e.target.value) : "")
            }
            disabled={busy || availableAssets.length === 0}
            className="rounded-md border border-zinc-200 bg-white px-2 py-1.5 text-xs font-medium text-zinc-700 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
          >
            <option value="">
              {availableAssets.length === 0
                ? "Every asset already on list"
                : "Add asset…"}
            </option>
            {availableAssets.map((a) => (
              <option key={a.id} value={a.id}>
                {a.symbol} — {a.name}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={busy || typeof selectedAssetId !== "number"}
            className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            Add
          </button>
          <button
            type="button"
            onClick={onTrackNew}
            disabled={busy}
            className="inline-flex items-center gap-1 rounded-md border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
            title="Track a symbol that isn't in your library yet"
          >
            <Plus className="h-3.5 w-3.5" />
            Track new…
          </button>
        </form>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {detail.items.length === 0 ? (
          <div className="m-4 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-400">
            No assets on this watchlist yet. Use the dropdown above to add one.
          </div>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={detail.items.map((i) => i.asset_id)}
              strategy={verticalListSortingStrategy}
            >
              <ul className="space-y-1">
                {detail.items.map((item) => (
                  <SortableItemRow
                    key={item.asset_id}
                    item={item}
                    onRemove={() => onRemoveAsset(item.asset_id)}
                    busy={busy}
                  />
                ))}
              </ul>
            </SortableContext>
          </DndContext>
        )}
      </div>
    </>
  );
}

interface SortableItemRowProps {
  item: WatchlistItem;
  onRemove: () => void;
  busy: boolean;
}

function SortableItemRow({ item, onRemove, busy }: SortableItemRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.asset_id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };

  return (
    <li
      ref={setNodeRef}
      style={style}
      className="group flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-2 py-2 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        className="cursor-grab touch-none text-zinc-400 hover:text-zinc-600 active:cursor-grabbing dark:text-zinc-500 dark:hover:text-zinc-300"
        aria-label="Reorder"
      >
        <GripVertical className="h-4 w-4" />
      </button>
      <span className="w-12 text-sm font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
        {item.symbol}
      </span>
      <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
        {item.asset_type}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-zinc-500 dark:text-zinc-400">
        {item.name}
      </span>
      <button
        type="button"
        onClick={onRemove}
        disabled={busy}
        aria-label={`Remove ${item.symbol}`}
        className="rounded p-1 text-zinc-400 opacity-0 hover:bg-rose-100 hover:text-rose-600 group-hover:opacity-100 disabled:opacity-30 dark:text-zinc-500 dark:hover:bg-rose-950 dark:hover:text-rose-400"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </li>
  );
}
