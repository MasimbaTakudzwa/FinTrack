import { useEffect, useState } from "react";
import { getHealth, HealthResponse } from "./api/client";
import { applyTheme, resolveTheme, useSettings } from "./stores/useSettings";
import "./App.css";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse }
  | { kind: "error"; message: string };

const POLL_INTERVAL_MS = 2000;

function App() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });
  const [tick, setTick] = useState(0);
  const themeMode = useSettings((s) => s.theme);

  useEffect(() => {
    applyTheme(resolveTheme(themeMode));
    if (themeMode !== "system") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme(resolveTheme("system"));
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [themeMode]);

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

  return (
    <main className="container">
      <h1>FinTrack</h1>
      <p className="subtitle">Market Intelligence — local build</p>
      <HealthBadge state={state} onRetry={() => setTick((t) => t + 1)} />
    </main>
  );
}

function HealthBadge({
  state,
  onRetry,
}: {
  state: HealthState;
  onRetry: () => void;
}) {
  if (state.kind === "loading") {
    return <div className="badge badge-loading">Checking sidecar…</div>;
  }
  if (state.kind === "ok") {
    return (
      <div className="badge badge-ok">
        <span className="dot" /> Sidecar healthy — v{state.data.version}
      </div>
    );
  }
  return (
    <div className="badge badge-error">
      <div className="badge-error-row">
        <span className="dot" /> Sidecar unreachable
      </div>
      <button type="button" className="retry" onClick={onRetry}>
        Retry
      </button>
      <pre className="error-detail">{state.message}</pre>
    </div>
  );
}

export default App;
