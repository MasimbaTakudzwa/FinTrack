import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Check,
  Loader2,
  Monitor,
  Moon,
  RefreshCw,
  Sun,
} from "lucide-react";
import {
  type AppConfig,
  type ConfigUpdateValue,
  type SettingEntry,
  type SettingSource,
  getConfig,
  putConfig,
} from "../api/client";
import { type ThemeMode, useSettings } from "../stores/useSettings";

interface LoadState {
  config: AppConfig | null;
  loading: boolean;
  error: string | null;
}

type FormValue = ConfigUpdateValue | null;

const INITIAL: LoadState = { config: null, loading: true, error: null };

export function Settings() {
  const [state, setState] = useState<LoadState>(INITIAL);
  const [form, setForm] = useState<Record<string, FormValue>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const config = await getConfig(controller.signal);
        if (cancelled) return;
        setState({ config, loading: false, error: null });
        setForm(emptyForm(config.settings));
        setSaveError(null);
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        setState({
          config: null,
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [tick]);

  const dirty = useMemo(() => {
    if (!state.config) return {} as Record<string, ConfigUpdateValue>;
    return collectDirty(state.config.settings, form);
  }, [state.config, form]);
  const hasDirty = Object.keys(dirty).length > 0;

  const refresh = () => {
    setState((s) => ({ ...s, loading: true }));
    setSaveNotice(null);
    setTick((t) => t + 1);
  };

  const revert = () => {
    if (!state.config) return;
    setForm(emptyForm(state.config.settings));
    setSaveError(null);
    setSaveNotice(null);
  };

  const save = async () => {
    if (!hasDirty) return;
    setSaving(true);
    setSaveError(null);
    setSaveNotice(null);
    try {
      const next = await putConfig(dirty);
      setState({ config: next, loading: false, error: null });
      setForm(emptyForm(next.settings));
      setSaveNotice("Settings saved.");
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            Settings
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Appearance, data sources, and runtime information.
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={state.loading}
          className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${state.loading ? "animate-spin" : ""}`}
          />
          Reload
        </button>
      </div>

      <ThemeSection />

      {state.loading && (
        <div className="rounded-lg border border-zinc-200 bg-white p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
          <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
          Loading current configuration…
        </div>
      )}

      {state.error && !state.loading && (
        <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          <AlertCircle className="mr-1 inline h-4 w-4 align-text-bottom" />
          Failed to load config: {state.error}
        </div>
      )}

      {state.config && (
        <>
          <section className="mt-6">
            <SectionHeading
              title="Data & scheduler"
              hint="Applied immediately. Scheduler jobs are rescheduled in place."
            />
            <div className="mt-3 space-y-3">
              {state.config.settings.map((entry) => (
                <SettingRow
                  key={entry.key}
                  entry={entry}
                  value={form[entry.key]}
                  onChange={(v) =>
                    setForm((f) => ({ ...f, [entry.key]: v }))
                  }
                />
              ))}
            </div>
          </section>

          <section className="mt-6">
            <SectionHeading
              title="Runtime information"
              hint="Read-only. Set via environment variables at launch."
            />
            <ReadonlyPanel readonly={state.config.readonly} />
          </section>
        </>
      )}

      {state.config && hasDirty && (
        <div className="sticky bottom-4 z-10 mt-6">
          <div className="flex items-center justify-between rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 shadow-sm dark:border-indigo-900 dark:bg-indigo-950">
            <div className="text-sm text-indigo-900 dark:text-indigo-100">
              {Object.keys(dirty).length} unsaved change
              {Object.keys(dirty).length === 1 ? "" : "s"}
              {saveError && (
                <span className="ml-2 text-rose-700 dark:text-rose-300">
                  — {saveError}
                </span>
              )}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={revert}
                disabled={saving}
                className="rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
              >
                Revert
              </button>
              <button
                type="button"
                onClick={save}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-md border border-indigo-600 bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                {saving ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="h-3.5 w-3.5" />
                )}
                Save changes
              </button>
            </div>
          </div>
        </div>
      )}

      {saveNotice && !hasDirty && (
        <p className="mt-4 text-xs text-emerald-600 dark:text-emerald-400">
          <Check className="mr-1 inline h-3 w-3 align-text-bottom" />
          {saveNotice}
        </p>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Theme section (client-only, uses local store)
// ----------------------------------------------------------------------------

const THEME_OPTIONS: { mode: ThemeMode; label: string; icon: typeof Sun }[] = [
  { mode: "system", label: "System", icon: Monitor },
  { mode: "light", label: "Light", icon: Sun },
  { mode: "dark", label: "Dark", icon: Moon },
];

function ThemeSection() {
  const theme = useSettings((s) => s.theme);
  const setTheme = useSettings((s) => s.setTheme);

  return (
    <section className="mb-6">
      <SectionHeading
        title="Appearance"
        hint="Stored locally in your browser."
      />
      <div
        role="radiogroup"
        aria-label="Theme"
        className="mt-3 grid grid-cols-3 gap-2"
      >
        {THEME_OPTIONS.map(({ mode, label, icon: Icon }) => {
          const active = theme === mode;
          return (
            <button
              key={mode}
              role="radio"
              aria-checked={active}
              type="button"
              onClick={() => setTheme(mode)}
              className={`flex items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-medium transition-colors ${
                active
                  ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:border-indigo-500 dark:bg-indigo-950 dark:text-indigo-200"
                  : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          );
        })}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------
// SettingRow — renders one mutable backend setting
// ----------------------------------------------------------------------------

function SettingRow({
  entry,
  value,
  onChange,
}: {
  entry: SettingEntry;
  value: FormValue;
  onChange: (v: FormValue) => void;
}) {
  const bounds =
    entry.type === "int" && (entry.min !== null || entry.max !== null)
      ? ` (${entry.min ?? ""}${entry.min !== null || entry.max !== null ? "–" : ""}${entry.max ?? ""})`
      : "";

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {entry.label}
              {bounds && (
                <span className="ml-1 text-xs font-normal text-zinc-400">
                  {bounds}
                </span>
              )}
            </span>
            <SourceBadge source={entry.source} envName={entry.env_name} />
          </div>
          {entry.description && (
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              {entry.description}
            </p>
          )}
        </div>
        <div className="flex-shrink-0">
          <SettingInput entry={entry} value={value} onChange={onChange} />
        </div>
      </div>
    </div>
  );
}

function SettingInput({
  entry,
  value,
  onChange,
}: {
  entry: SettingEntry;
  value: FormValue;
  onChange: (v: FormValue) => void;
}) {
  if (entry.type === "bool") {
    const checked = (value ?? entry.value ?? false) as boolean;
    return (
      <label className="inline-flex cursor-pointer items-center">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="peer sr-only"
        />
        <span className="relative h-6 w-11 rounded-full bg-zinc-200 transition-colors peer-checked:bg-indigo-600 dark:bg-zinc-700 peer-checked:dark:bg-indigo-500">
          <span
            className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${
              checked ? "translate-x-5" : "translate-x-0"
            }`}
          />
        </span>
      </label>
    );
  }

  if (entry.type === "int") {
    const current = (value ?? entry.value ?? 0) as number;
    return (
      <input
        type="number"
        value={String(current)}
        min={entry.min ?? undefined}
        max={entry.max ?? undefined}
        onChange={(e) => {
          const n = Number(e.target.value);
          onChange(Number.isFinite(n) ? n : 0);
        }}
        className="w-24 rounded-md border border-zinc-200 bg-white px-2 py-1 text-right text-sm tabular-nums text-zinc-900 focus:border-indigo-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      />
    );
  }

  // secret / string
  const isSecret = entry.type === "secret";
  const placeholder =
    isSecret && entry.has_value ? "Stored (hidden) — type to replace" : "";
  const current = (value as string | null) ?? "";

  return (
    <div className="flex items-center gap-2">
      <input
        type={isSecret ? "password" : "text"}
        value={current}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-56 rounded-md border border-zinc-200 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-indigo-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      />
      {isSecret && entry.source === "db" && (
        <button
          type="button"
          onClick={() => onChange("")}
          title="Clear stored value"
          className="rounded-md border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
        >
          Clear
        </button>
      )}
    </div>
  );
}

function SourceBadge({
  source,
  envName,
}: {
  source: SettingSource;
  envName: string | null;
}) {
  const styles: Record<SettingSource, string> = {
    default:
      "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
    env: "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300",
    db: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  };
  const title =
    source === "env" && envName
      ? `Set via ${envName} environment variable`
      : source === "db"
        ? "Overridden via settings"
        : "Using built-in default";
  return (
    <span
      title={title}
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${styles[source]}`}
    >
      {source}
    </span>
  );
}

// ----------------------------------------------------------------------------
// Read-only panel
// ----------------------------------------------------------------------------

function ReadonlyPanel({
  readonly: ro,
}: {
  readonly: AppConfig["readonly"];
}) {
  const rows: [string, string][] = [
    ["Database path", ro.db_path],
    ["Sidecar port", String(ro.port)],
    ["Log level", ro.log_level],
    ["Scheduler enabled", ro.enable_scheduler ? "yes" : "no"],
    ["Seed defaults on start", ro.enable_seed ? "yes" : "no"],
  ];
  return (
    <dl className="mt-3 divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-950">
      {rows.map(([label, value]) => (
        <div
          key={label}
          className="flex items-start justify-between gap-4 px-4 py-3"
        >
          <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            {label}
          </dt>
          <dd className="break-all text-right font-mono text-xs text-zinc-700 dark:text-zinc-300">
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

function SectionHeading({ title, hint }: { title: string; hint?: string }) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {title}
      </h3>
      {hint && (
        <p className="mt-0.5 text-[11px] text-zinc-400 dark:text-zinc-500">
          {hint}
        </p>
      )}
    </div>
  );
}

/** Initialize form state so `null` means "no change". */
function emptyForm(entries: SettingEntry[]): Record<string, FormValue> {
  const out: Record<string, FormValue> = {};
  for (const e of entries) out[e.key] = null;
  return out;
}

/** Diff form state against loaded config — returns only changed values. */
function collectDirty(
  entries: SettingEntry[],
  form: Record<string, FormValue>,
): Record<string, ConfigUpdateValue> {
  const out: Record<string, ConfigUpdateValue> = {};
  const byKey = new Map(entries.map((e) => [e.key, e]));

  for (const [key, raw] of Object.entries(form)) {
    if (raw === null) continue;
    const entry = byKey.get(key);
    if (!entry) continue;

    // Secret special-casing: empty string always sent (intent: clear).
    if (entry.type === "secret") {
      if (raw === "" && !entry.has_value) continue; // no-op clear
      out[key] = raw as ConfigUpdateValue;
      continue;
    }

    // Skip if value equals current (nothing actually changed).
    if (raw === entry.value) continue;
    out[key] = raw as ConfigUpdateValue;
  }
  return out;
}
