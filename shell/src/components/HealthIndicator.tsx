import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { getHealth, type HealthResponse } from "../api/client";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse }
  | { kind: "error"; message: string };

const POLL_INTERVAL_MS = 2000;

export function HealthIndicator() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    (async () => {
      try {
        const data = await getHealth(controller.signal);
        if (!cancelled) setState({ kind: "ok", data });
      } catch (err) {
        if (!cancelled && !controller.signal.aborted) {
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [tick]);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  if (state.kind === "loading") {
    return (
      <div
        title="Checking sidecar…"
        className="inline-flex items-center gap-2 rounded-full border border-zinc-200 bg-zinc-50 px-3 py-1 text-xs font-medium text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        <span>Checking…</span>
      </div>
    );
  }
  if (state.kind === "ok") {
    return (
      <div
        title={`Sidecar v${state.data.version} — ${state.data.status}`}
        className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
      >
        <CheckCircle2 className="h-3.5 w-3.5" />
        <span>Live · v{state.data.version}</span>
      </div>
    );
  }
  return (
    <button
      type="button"
      title={state.message}
      onClick={() => setTick((t) => t + 1)}
      className="inline-flex items-center gap-2 rounded-full border border-rose-200 bg-rose-50 px-3 py-1 text-xs font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-300 dark:hover:bg-rose-900"
    >
      <AlertCircle className="h-3.5 w-3.5" />
      <span>Offline · retry</span>
    </button>
  );
}
