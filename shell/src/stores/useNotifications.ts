/**
 * In-app notification center state.
 *
 * We track a single ``lastSeenAt`` unix-ms timestamp. Any alert that
 * triggered AFTER this timestamp counts as unread, so the Header bell
 * shows a badge. Opening the dropdown calls ``markAllSeen()`` which
 * bumps the timestamp to ``Date.now()``.
 *
 * Persisted to localStorage so the badge state survives window reloads.
 * This lives apart from the OS-notification handshake (``useAlertNotifier``)
 * on purpose — in-app history is a pure UI concern; the DB still owns
 * the authoritative ``triggered_at`` / ``notified_at`` columns.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface NotificationsState {
  /** Unix ms. Alerts with triggered_at > lastSeenAt are "unread". */
  lastSeenAt: number;
  markAllSeen: () => void;
}

export const useNotifications = create<NotificationsState>()(
  persist(
    (set) => ({
      // Default to epoch 0 so every existing triggered alert is "unread"
      // the first time the user opens the app — gives them an immediate
      // prompt to check the center.
      lastSeenAt: 0,
      markAllSeen: () => set({ lastSeenAt: Date.now() }),
    }),
    { name: "fintrack-notifications" },
  ),
);
