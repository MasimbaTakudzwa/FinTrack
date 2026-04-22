/**
 * In-app notification center.
 *
 * Why this exists on top of the OS-native notifier:
 *   1. Persistent history — OS notifications disappear; the bell always
 *      shows what fired recently.
 *   2. Works regardless of OS permissions. Users who denied notification
 *      perms still see alerts here.
 *   3. In dev mode Tauri's unsigned binary has ``CFBundleIdentifier=NULL``
 *      so macOS shows the notification as coming from "Terminal" (or
 *      whatever parent spawned ``tauri dev``). In a bundled ``.app``
 *      this resolves to "FinTrack" via ``identifier`` in tauri.conf.json,
 *      but until then the in-app bell is the canonical surface.
 *
 * Polls ``listAlerts()`` every ``POLL_INTERVAL_MS`` and filters client-side
 * for ``triggered_at != null``. Unread count is anything triggered AFTER
 * the ``lastSeenAt`` timestamp in the ``useNotifications`` store.
 */

import { Bell, BellRing, ExternalLink } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { listAlerts, type PriceAlert } from "../api/client";
import { useNotifications } from "../stores/useNotifications";

const POLL_INTERVAL_MS = 60_000;
const MAX_VISIBLE = 12;

// SQLite stores naive UTC; coerce if there's no offset/Z suffix.
function parseIsoMs(iso: string): number {
  const normalised = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return Date.parse(normalised);
}

function timeAgo(iso: string): string {
  const ms = Date.now() - parseIsoMs(iso);
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return iso.slice(0, 10);
}

interface FeedState {
  alerts: PriceAlert[];
  loading: boolean;
  error: string | null;
}

const INITIAL: FeedState = { alerts: [], loading: true, error: null };

function byTriggeredDesc(a: PriceAlert, b: PriceAlert): number {
  const ax = a.triggered_at ? parseIsoMs(a.triggered_at) : 0;
  const bx = b.triggered_at ? parseIsoMs(b.triggered_at) : 0;
  return bx - ax;
}

export function NotificationCenter() {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<FeedState>(INITIAL);
  const lastSeenAt = useNotifications((s) => s.lastSeenAt);
  const markAllSeen = useNotifications((s) => s.markAllSeen);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  // ---- Poll ----------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const fetchOnce = (): void => {
      if (cancelled) return;
      listAlerts()
        .then((list) => {
          if (cancelled) return;
          const triggered = list.alerts
            .filter((a) => a.triggered_at !== null)
            .sort(byTriggeredDesc);
          setState({ alerts: triggered, loading: false, error: null });
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setState({
            alerts: [],
            loading: false,
            error: err instanceof Error ? err.message : String(err),
          });
        })
        .finally(() => {
          if (!cancelled) timer = setTimeout(fetchOnce, POLL_INTERVAL_MS);
        });
    };

    // First fetch delayed slightly so the sidecar has time to come up
    // on a cold Tauri launch (same pattern as useAlertNotifier).
    timer = setTimeout(fetchOnce, 1_500);

    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, []);

  // ---- Close on click-outside / Escape -------------------------------
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!panelRef.current || !buttonRef.current) return;
      const target = e.target as Node;
      if (panelRef.current.contains(target) || buttonRef.current.contains(target))
        return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // ---- Unread count --------------------------------------------------
  const unreadCount = useMemo(() => {
    if (state.alerts.length === 0) return 0;
    return state.alerts.filter((a) => {
      if (!a.triggered_at) return false;
      return parseIsoMs(a.triggered_at) > lastSeenAt;
    }).length;
  }, [state.alerts, lastSeenAt]);

  const onToggle = () => {
    const next = !open;
    setOpen(next);
    if (next && unreadCount > 0) markAllSeen();
  };

  const isUnread = (a: PriceAlert): boolean => {
    if (!a.triggered_at) return false;
    return parseIsoMs(a.triggered_at) > lastSeenAt;
  };

  const displayed = state.alerts.slice(0, MAX_VISIBLE);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={onToggle}
        aria-label="Notifications"
        className="relative inline-flex h-8 w-8 items-center justify-center rounded-md border border-zinc-200 bg-white text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
      >
        {unreadCount > 0 ? (
          <BellRing className="h-4 w-4 text-amber-600 dark:text-amber-400" />
        ) : (
          <Bell className="h-4 w-4" />
        )}
        {unreadCount > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-semibold text-white">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div
          ref={panelRef}
          className="absolute right-0 top-10 z-40 w-80 overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950"
        >
          <div className="flex items-center justify-between border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              <Bell className="h-3.5 w-3.5" />
              Notifications
            </div>
            <Link
              to="/alerts"
              onClick={() => setOpen(false)}
              className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 hover:underline dark:text-emerald-300"
            >
              Manage alerts
              <ExternalLink className="h-3 w-3" />
            </Link>
          </div>

          <div className="max-h-96 overflow-y-auto">
            {state.loading ? (
              <div className="px-3 py-6 text-center text-xs text-zinc-500 dark:text-zinc-400">
                Loading…
              </div>
            ) : state.error ? (
              <div className="px-3 py-4 text-xs text-rose-600 dark:text-rose-400">
                Couldn&rsquo;t load alerts: {state.error}
              </div>
            ) : displayed.length === 0 ? (
              <div className="flex flex-col items-center gap-1 px-3 py-8 text-center text-xs text-zinc-500 dark:text-zinc-400">
                <Bell className="h-5 w-5 text-zinc-300 dark:text-zinc-700" />
                <span>No alerts have triggered yet.</span>
                <Link
                  to="/alerts"
                  onClick={() => setOpen(false)}
                  className="mt-1 font-medium text-emerald-700 hover:underline dark:text-emerald-300"
                >
                  Create one →
                </Link>
              </div>
            ) : (
              <ul className="divide-y divide-zinc-100 dark:divide-zinc-900">
                {displayed.map((a) => (
                  <NotificationRow
                    key={a.id}
                    alert={a}
                    unread={isUnread(a)}
                    onNavigate={() => setOpen(false)}
                  />
                ))}
              </ul>
            )}
          </div>

          {state.alerts.length > MAX_VISIBLE && (
            <div className="border-t border-zinc-200 px-3 py-2 text-center text-[11px] text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
              Showing most recent {MAX_VISIBLE} of {state.alerts.length}.{" "}
              <Link
                to="/alerts"
                onClick={() => setOpen(false)}
                className="font-medium text-emerald-700 hover:underline dark:text-emerald-300"
              >
                See all →
              </Link>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function NotificationRow({
  alert,
  unread,
  onNavigate,
}: {
  alert: PriceAlert;
  unread: boolean;
  onNavigate: () => void;
}) {
  const dirArrow = alert.direction === "above" ? "↑" : "↓";
  const dirTone =
    alert.direction === "above"
      ? "text-emerald-600 dark:text-emerald-400"
      : "text-rose-600 dark:text-rose-400";

  return (
    <li
      className={
        unread
          ? "relative bg-amber-50/60 px-3 py-2.5 dark:bg-amber-950/30"
          : "px-3 py-2.5"
      }
    >
      {unread && (
        <span className="absolute left-0 top-0 h-full w-0.5 bg-amber-500" />
      )}
      <Link
        to={`/assets/${encodeURIComponent(alert.symbol)}`}
        onClick={onNavigate}
        className="block"
      >
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            {alert.symbol}{" "}
            <span className={`font-mono text-xs font-bold ${dirTone}`}>
              {dirArrow}
            </span>{" "}
            <span className="font-mono text-xs font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
              {alert.threshold}
            </span>
          </span>
          <span className="shrink-0 text-[10px] font-medium tabular-nums text-zinc-400 dark:text-zinc-500">
            {alert.triggered_at ? timeAgo(alert.triggered_at) : ""}
          </span>
        </div>
        <div className="mt-0.5 truncate text-[11px] text-zinc-500 dark:text-zinc-400">
          {alert.direction === "above" ? "Crossed above" : "Dropped below"}{" "}
          {alert.threshold}
          {alert.last_price !== null && <> · last {alert.last_price}</>}
        </div>
        {alert.note && (
          <div className="mt-0.5 truncate text-[11px] italic text-zinc-400 dark:text-zinc-500">
            {alert.note}
          </div>
        )}
      </Link>
    </li>
  );
}
