import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Newspaper, RefreshCw } from "lucide-react";
import {
  type Article,
  type Asset,
  type SentimentBucket,
  listAssets,
  listNews,
} from "../api/client";
import { NewsList } from "../components/NewsList";

const ALL = "__all__";
const PAGE_LIMIT = 100;

const SENTIMENT_BUCKETS: { key: SentimentBucket | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "positive", label: "Positive" },
  { key: "neutral", label: "Neutral" },
  { key: "negative", label: "Negative" },
];

interface State {
  articles: Article[];
  assets: Asset[];
  loading: boolean;
  error: string | null;
}

const INITIAL: State = {
  articles: [],
  assets: [],
  loading: true,
  error: null,
};

export function News() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlSymbol = searchParams.get("symbol")?.toUpperCase() ?? null;
  const urlSentiment = parseSentimentParam(searchParams.get("sentiment"));
  const [state, setState] = useState<State>(INITIAL);
  const [tick, setTick] = useState(0);

  const selected = urlSymbol ?? ALL;

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const [assets, articleList] = await Promise.all([
          listAssets({ activeOnly: false, signal: controller.signal }),
          listNews({
            symbol: urlSymbol ?? undefined,
            sentiment: urlSentiment ?? undefined,
            limit: PAGE_LIMIT,
            signal: controller.signal,
          }),
        ]);
        if (cancelled) return;
        setState({
          assets,
          articles: articleList.articles,
          loading: false,
          error: null,
        });
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        setState((prev) => ({
          ...prev,
          articles: [],
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        }));
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [urlSymbol, urlSentiment, tick]);

  const onFilterChange = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value === ALL) {
      next.delete("symbol");
    } else {
      next.set("symbol", value);
    }
    setSearchParams(next);
    // Reset loading here (event handler, NOT inside useEffect) so the user
    // sees a spinner while the new filter's articles are fetched.
    setState((s) => ({ ...s, loading: true, error: null }));
  };

  const onSentimentChange = (bucket: SentimentBucket | "all") => {
    const next = new URLSearchParams(searchParams);
    if (bucket === "all") {
      next.delete("sentiment");
    } else {
      next.set("sentiment", bucket);
    }
    setSearchParams(next);
    setState((s) => ({ ...s, loading: true, error: null }));
  };

  const refresh = () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    setTick((t) => t + 1);
  };

  const sortedAssets = useMemo(
    () => [...state.assets].sort((a, b) => a.symbol.localeCompare(b.symbol)),
    [state.assets],
  );

  const articlesByDay = useMemo(() => groupByDay(state.articles), [state.articles]);

  return (
    <div className="p-6">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            <Newspaper className="h-5 w-5 text-zinc-400" />
            News
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {state.loading
              ? "Loading…"
              : `${state.articles.length} article${state.articles.length === 1 ? "" : "s"}${
                  urlSymbol ? ` for ${urlSymbol}` : ""
                }${
                  urlSentiment ? ` · ${urlSentiment}` : ""
                }`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SentimentTabs
            selected={urlSentiment ?? "all"}
            onChange={onSentimentChange}
          />
          <label
            htmlFor="news-symbol-filter"
            className="text-xs font-medium text-zinc-600 dark:text-zinc-300"
          >
            Symbol
          </label>
          <select
            id="news-symbol-filter"
            value={selected}
            onChange={(e) => onFilterChange(e.target.value)}
            className="rounded-md border border-zinc-200 bg-white px-2 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <option value={ALL}>All symbols</option>
            {sortedAssets.map((a) => (
              <option key={a.id} value={a.symbol}>
                {a.symbol} — {a.name}
              </option>
            ))}
          </select>
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

      {articlesByDay.length === 0 && !state.loading ? (
        <NewsList
          articles={[]}
          loading={false}
          error={state.error}
          emptyMessage={
            urlSymbol
              ? `No news yet for ${urlSymbol}. Try another symbol or refresh in a few minutes.`
              : "No news yet — the ingest_news job runs every 15 minutes."
          }
        />
      ) : (
        <div className="space-y-6">
          {articlesByDay.map((group) => (
            <section
              key={group.day}
              className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950"
            >
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                {group.label}
              </h3>
              <NewsList
                articles={group.articles}
                loading={false}
                error={null}
                density="comfortable"
              />
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function SentimentTabs({
  selected,
  onChange,
}: {
  selected: SentimentBucket | "all";
  onChange: (bucket: SentimentBucket | "all") => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Filter by sentiment"
      className="inline-flex rounded-md border border-zinc-200 bg-white p-0.5 dark:border-zinc-700 dark:bg-zinc-900"
    >
      {SENTIMENT_BUCKETS.map(({ key, label }) => {
        const active = selected === key;
        const palette =
          key === "positive"
            ? "data-[active=true]:bg-emerald-100 data-[active=true]:text-emerald-800 dark:data-[active=true]:bg-emerald-900/40 dark:data-[active=true]:text-emerald-300"
            : key === "negative"
              ? "data-[active=true]:bg-rose-100 data-[active=true]:text-rose-800 dark:data-[active=true]:bg-rose-900/40 dark:data-[active=true]:text-rose-300"
              : "data-[active=true]:bg-zinc-100 data-[active=true]:text-zinc-900 dark:data-[active=true]:bg-zinc-800 dark:data-[active=true]:text-zinc-100";
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={active}
            data-active={active}
            onClick={() => onChange(key)}
            className={`rounded px-2.5 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800 ${palette}`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

function parseSentimentParam(raw: string | null): SentimentBucket | null {
  if (raw === "positive" || raw === "neutral" || raw === "negative") return raw;
  return null;
}

interface DayGroup {
  day: string;
  label: string;
  articles: Article[];
}

function groupByDay(articles: Article[]): DayGroup[] {
  const groups = new Map<string, Article[]>();
  for (const a of articles) {
    const day = a.published_at.slice(0, 10); // YYYY-MM-DD in UTC from ISO
    const bucket = groups.get(day);
    if (bucket) {
      bucket.push(a);
    } else {
      groups.set(day, [a]);
    }
  }
  const today = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  return [...groups.entries()]
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([day, list]) => ({
      day,
      label: day === today ? "Today" : day === yesterday ? "Yesterday" : day,
      articles: list,
    }));
}
