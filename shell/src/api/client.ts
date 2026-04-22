import { invoke } from "@tauri-apps/api/core";

let baseUrlPromise: Promise<string> | null = null;

export async function getBaseUrl(): Promise<string> {
  if (!baseUrlPromise) {
    baseUrlPromise = (async () => {
      const port = await invoke<number>("get_sidecar_port");
      if (!port) {
        throw new Error("Sidecar port is 0 — shell did not spawn the sidecar");
      }
      return `http://127.0.0.1:${port}`;
    })();
  }
  return baseUrlPromise;
}

export interface HealthResponse {
  status: string;
  version: string;
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const base = await getBaseUrl();
  const res = await fetch(`${base}/api/health/`, { signal });
  if (!res.ok) {
    throw new Error(`Health check returned HTTP ${res.status}`);
  }
  return (await res.json()) as HealthResponse;
}
