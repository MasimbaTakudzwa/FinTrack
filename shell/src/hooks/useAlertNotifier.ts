/**
 * Shell-side alert notifier.
 *
 * Polls ``/api/alerts/pending-notifications/`` every ``POLL_INTERVAL_MS``,
 * fires a native OS notification for each pending alert, then POSTs to
 * ``/mark-notified/`` so the sidecar stops returning them.
 *
 * Permission handling:
 *   - On mount, request permission once. If denied, the hook still runs but
 *     notifications are silently dropped (we still mark them as notified so
 *     the list doesn't grow unbounded).
 *
 * Resilience:
 *   - If any step throws (network, plugin not available in dev browser, etc.)
 *     the error is logged and the next poll tick retries cleanly.
 *   - A crash between fire and mark-notified simply replays on the next poll
 *     — at worst a duplicate ping, never a lost one.
 */

import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";
import { useEffect, useRef } from "react";

import {
  listPendingAlertNotifications,
  markAlertNotified,
  type PriceAlert,
} from "../api/client";

const POLL_INTERVAL_MS = 30_000;

function formatTitle(a: PriceAlert): string {
  const arrow = a.direction === "above" ? "↑" : "↓";
  return `${a.symbol} ${arrow} ${a.threshold}`;
}

function formatBody(a: PriceAlert): string {
  const direction = a.direction === "above" ? "rose above" : "dropped below";
  const priceStr = a.last_price ? `at ${a.last_price}` : "";
  const noteStr = a.note ? ` — ${a.note}` : "";
  return `${a.asset_name} ${direction} ${a.threshold} ${priceStr}${noteStr}`.trim();
}

async function ensurePermission(): Promise<boolean> {
  try {
    if (await isPermissionGranted()) return true;
    const result = await requestPermission();
    return result === "granted";
  } catch (err) {
    console.warn("[alerts] notification permission check failed:", err);
    return false;
  }
}

async function processPending(
  permissionOk: boolean,
  signal: AbortSignal,
): Promise<void> {
  const list = await listPendingAlertNotifications(signal);
  if (list.count === 0) return;

  for (const alert of list.alerts) {
    if (signal.aborted) return;
    if (permissionOk) {
      try {
        sendNotification({
          title: formatTitle(alert),
          body: formatBody(alert),
        });
      } catch (err) {
        console.warn("[alerts] sendNotification failed:", err);
      }
    }
    try {
      await markAlertNotified(alert.id, signal);
    } catch (err) {
      console.warn(
        `[alerts] markAlertNotified failed for id=${alert.id}:`,
        err,
      );
    }
  }
}

export function useAlertNotifier(): void {
  const permissionRef = useRef<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();

    const tick = (): void => {
      if (cancelled) return;
      void (async () => {
        try {
          if (permissionRef.current === null) {
            permissionRef.current = await ensurePermission();
          }
          await processPending(permissionRef.current, controller.signal);
        } catch (err) {
          // Network error, sidecar not up yet, or aborted on unmount — swallow,
          // retry next tick.
          console.warn("[alerts] poll tick failed:", err);
        } finally {
          if (!cancelled) {
            timer = setTimeout(tick, POLL_INTERVAL_MS);
          }
        }
      })();
    };

    // Fire first tick soon after mount so the dashboard shows notifications
    // quickly on launch (but not synchronously in the mount frame).
    timer = setTimeout(tick, 1_500);

    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, []);
}
