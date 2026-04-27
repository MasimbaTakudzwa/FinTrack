import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Loader2,
  Sparkles,
} from "lucide-react";
import {
  classifySentiment,
  listNews,
  type Article,
  type SentimentBucket,
  type SentimentTimeseriesPoint,
} from "../api/client";

/** Same thresholds as the candle-chart sentiment markers — keeps the
 *  drill-down panel and the chart visuals showing the same days. */
const SENTIMENT_DAY_THRESHOLD = 0.3;
const SENTIMENT_DAY_MIN_COUNT = 2;
const HEADLINES_PER_DAY_CAP = 4;
const NEWS_FETCH_LIMIT = 300;

interface Props {
  symbol: string;
  sentimentSeries: SentimentTimeseriesPoint[];
}

interface StrongDay {
  date: string; // YYYY-MM-DD UTC
  mean: number;
  count: number;
  bucket: SentimentBucket;
}

type State =
  | { status: "loading"; articles: null; error: null }
  | { status: "ready"; articles: Article[]; error: null }
  | { status: "error"; articles: null; error: string };

const INITIAL: State = { status: "loading", articles: null, error: null };

/**
 * Drill-down for the chart's sentiment markers — lists the days that
 * crossed the strong-signal threshold and exposes the actual headlines
 * that drove them. Hidden entirely when there are no qualifying days
 * (clutter avoidance — the panel only earns its real estate when there
 * are interesting events to show).
 *
 * Fetches ``/api/news/?symbol=...`` once with a generous limit and
 * groups the result client-side, avoiding the N-request fan-out a
 * naive per-day fetch would produce. If the corpus grows past
 * ``NEWS_FETCH_LIMIT`` we'll start losing earlier strong-day headlines —
 * acceptable for the 90-day window we surface today, and the panel
 * shows a "+N more" hint when the bucket is truncated.
 */
export function StrongSentimentDaysPanel({ symbol, sentimentSeries }: Props) {
  const strongDays = useMemo(() => extractStrongDays(sentimentSeries), [
    sentimentSeries,
  ]);

  const [state, setState] = useState<State>(INITIAL);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    // Skip the fetch entirely if there are no strong days to anchor it
    // — keeps the panel cheap when the asset has quiet news flow.
    if (strongDays.length === 0) {
      // setState in an event-driven way: the empty case renders nothing,
      // so we don't need to push state from the effect body. React 19's
      // set-state-in-effect rule is happy.
      return;
    }
    const ac = new AbortController();
    listNews({ symbol, limit: NEWS_FETCH_LIMIT, signal: ac.signal })
      .then((data) =>
        setState({ status: "ready", articles: data.articles, error: null }),
      )
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setState({
          status: "error",
          articles: null,
          error:
            err instanceof Error ? err.message : "Failed to load headlines",
        });
      });
    return () => ac.abort();
  }, [symbol, strongDays.length]);

  const articlesByDay = useMemo(() => {
    if (state.status !== "ready") return new Map<string, Article[]>();
    const map = new Map<string, Article[]>();
    for (const a of state.articles) {
      // Backend stores naive UTC; the slice path used elsewhere in the
      // codebase is "first 10 chars of published_at" which lines up with
      // the timeseries date format.
      const day = a.published_at.slice(0, 10);
      const bucket = map.get(day);
      if (bucket) {
        bucket.push(a);
      } else {
        map.set(day, [a]);
      }
    }
    return map;
  }, [state]);

  if (strongDays.length === 0) {
    // Don't earn the page real estate when there's nothing to surface.
    return null;
  }

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          <Sparkles className="h-4 w-4 text-indigo-500" />
          Strong sentiment days
          <span className="text-xs font-normal text-zinc-500 dark:text-zinc-400">
            · last 90d
          </span>
        </h2>
      </header>
      <Body
        state={state}
        strongDays={strongDays}
        articlesByDay={articlesByDay}
        expanded={expanded}
        onToggle={(date) => {
          setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(date)) next.delete(date);
            else next.add(date);
            return next;
          });
        }}
      />
    </section>
  );
}

function Body({
  state,
  strongDays,
  articlesByDay,
  expanded,
  onToggle,
}: {
  state: State;
  strongDays: StrongDay[];
  articlesByDay: Map<string, Article[]>;
  expanded: Set<string>;
  onToggle: (date: string) => void;
}) {
  if (state.status === "loading") {
    return (
      <div className="flex items-center gap-2 py-3 text-sm text-zinc-500 dark:text-zinc-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading headlines for the {strongDays.length} flagged day
        {strongDays.length === 1 ? "" : "s"}…
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
        {state.error}
      </div>
    );
  }

  return (
    <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
      {strongDays.map((day) => {
        const isOpen = expanded.has(day.date);
        const articles = articlesByDay.get(day.date) ?? [];
        return (
          <li key={day.date}>
            <button
              type="button"
              onClick={() => onToggle(day.date)}
              className="flex w-full items-center justify-between gap-3 py-2 text-left hover:bg-zinc-50/60 dark:hover:bg-zinc-800/40"
            >
              <DayHeadline day={day} articleCount={articles.length} />
              {isOpen ? (
                <ChevronDown className="h-4 w-4 shrink-0 text-zinc-400" />
              ) : (
                <ChevronRight className="h-4 w-4 shrink-0 text-zinc-400" />
              )}
            </button>
            {isOpen && (
              <DayHeadlines
                date={day.date}
                articles={articles}
                totalCount={day.count}
              />
            )}
          </li>
        );
      })}
    </ul>
  );
}

function DayHeadline({
  day,
  articleCount,
}: {
  day: StrongDay;
  articleCount: number;
}) {
  const palette = {
    positive:
      "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    neutral:
      "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
    negative:
      "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  }[day.bucket];
  const Icon = day.mean >= 0 ? ArrowUp : ArrowDown;
  return (
    <span className="flex items-center gap-3 text-sm">
      <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
        {day.date}
      </span>
      <span
        className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${palette}`}
      >
        <Icon className="h-3 w-3" />
        {day.bucket} {day.mean >= 0 ? "+" : ""}
        {day.mean.toFixed(2)}
      </span>
      <span className="text-xs text-zinc-500 dark:text-zinc-400">
        {day.count} headline{day.count === 1 ? "" : "s"}
        {articleCount > 0 && articleCount < day.count
          ? ` · ${articleCount} loaded`
          : ""}
      </span>
    </span>
  );
}

function DayHeadlines({
  date,
  articles,
  totalCount,
}: {
  date: string;
  articles: Article[];
  totalCount: number;
}) {
  if (articles.length === 0) {
    return (
      <p className="px-2 pb-3 text-xs text-zinc-500 dark:text-zinc-400">
        Headlines from {date} aren't in the recent fetch — they may have
        rolled out of the cached window. Try the News page for the
        symbol-filtered view.
      </p>
    );
  }
  const visible = articles.slice(0, HEADLINES_PER_DAY_CAP);
  const hidden = Math.max(totalCount - visible.length, 0);
  return (
    <ul className="space-y-1.5 pb-3 pl-6">
      {visible.map((a) => (
        <li key={a.id} className="text-sm">
          <a
            href={a.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-start gap-1 text-zinc-700 hover:text-emerald-700 dark:text-zinc-300 dark:hover:text-emerald-400"
          >
            <span className="break-words">{a.headline}</span>
            <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-zinc-400" />
          </a>
          <span className="ml-2 text-[11px] text-zinc-500 dark:text-zinc-400">
            {a.source}
            {a.sentiment !== null && (
              <>
                {" · "}
                <span
                  title={`VADER compound score: ${a.sentiment.toFixed(3)}`}
                >
                  {a.sentiment >= 0 ? "+" : ""}
                  {a.sentiment.toFixed(2)}
                </span>
              </>
            )}
          </span>
        </li>
      ))}
      {hidden > 0 && (
        <li className="text-[11px] text-zinc-500 dark:text-zinc-400">
          + {hidden} more headline{hidden === 1 ? "" : "s"} on this day
        </li>
      )}
    </ul>
  );
}

function extractStrongDays(
  series: SentimentTimeseriesPoint[],
): StrongDay[] {
  const out: StrongDay[] = [];
  for (const p of series) {
    if (Math.abs(p.mean) < SENTIMENT_DAY_THRESHOLD) continue;
    if (p.count < SENTIMENT_DAY_MIN_COUNT) continue;
    out.push({
      date: p.date,
      mean: p.mean,
      count: p.count,
      bucket: classifySentiment(p.mean),
    });
  }
  // Newest first — matches how users scan news lists.
  out.sort((a, b) => b.date.localeCompare(a.date));
  return out;
}
