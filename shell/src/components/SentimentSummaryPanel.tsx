import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Loader2, Minus, Sparkles } from "lucide-react";
import {
  classifySentiment,
  getSentimentSummary,
  getSentimentTimeseries,
  type SentimentBucket,
  type SentimentSummary,
  type SentimentTimeseries,
} from "../api/client";
import { SentimentTimelineChart } from "./SentimentTimelineChart";

type State =
  | { status: "loading"; data: null; series: null; error: null }
  | {
      status: "ready";
      data: SentimentSummary;
      series: SentimentTimeseries;
      error: null;
    }
  | { status: "error"; data: null; series: null; error: string };

const INITIAL: State = {
  status: "loading",
  data: null,
  series: null,
  error: null,
};

interface Props {
  symbol: string;
  /** Window length for the rollup (defaults to 30 days). */
  days?: number;
}

/**
 * Per-asset news sentiment rollup. Renders the average compound score in
 * the last N days, the positive/neutral/negative bucket distribution, and
 * the unscored backlog when present.
 *
 * Computed server-side via a single aggregate query so the panel stays
 * cheap even as the news corpus grows. We re-fetch on `symbol`/`days`
 * change via the `key` prop, which keeps state transitions clean for the
 * React 19 `set-state-in-effect` rule.
 */
export function SentimentSummaryPanel({ symbol, days = 30 }: Props) {
  const [state, setState] = useState<State>(INITIAL);

  useEffect(() => {
    const ac = new AbortController();
    Promise.all([
      getSentimentSummary(symbol, { days, signal: ac.signal }),
      getSentimentTimeseries(symbol, { days, signal: ac.signal }),
    ])
      .then(([summary, series]) =>
        setState({
          status: "ready",
          data: summary,
          series,
          error: null,
        }),
      )
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setState({
          status: "error",
          data: null,
          series: null,
          error: err instanceof Error ? err.message : "Failed to load sentiment",
        });
      });
    return () => ac.abort();
  }, [symbol, days]);

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          <Sparkles className="h-4 w-4 text-indigo-500" />
          News sentiment
          <span className="text-xs font-normal text-zinc-500 dark:text-zinc-400">
            · last {days}d
          </span>
        </h2>
      </header>
      <Body state={state} />
    </section>
  );
}

function Body({ state }: { state: State }) {
  if (state.status === "loading") {
    return (
      <div className="flex items-center gap-2 py-3 text-sm text-zinc-500 dark:text-zinc-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>Aggregating headlines…</span>
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

  const { total, scored, unscored, positive, neutral, negative, mean } =
    state.data;

  if (total === 0) {
    return (
      <div className="py-3 text-sm text-zinc-500 dark:text-zinc-400">
        No news in the last {state.data.days} days.
      </div>
    );
  }

  const bucket: SentimentBucket = classifySentiment(mean);
  const meanLabel = mean === null ? "—" : formatScore(mean);

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-2xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
            {meanLabel}
          </div>
          <div className="text-xs uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            avg compound score
          </div>
        </div>
        <BucketBadge bucket={bucket} mean={mean} />
      </div>
      {state.series.points.length > 0 && (
        <div className="rounded-md border border-zinc-200 bg-zinc-50 p-2 dark:border-zinc-800 dark:bg-zinc-950">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Daily compound · {state.series.points.length} day
            {state.series.points.length === 1 ? "" : "s"} with news
          </div>
          <SentimentTimelineChart points={state.series.points} />
        </div>
      )}
      <DistributionBar
        positive={positive}
        neutral={neutral}
        negative={negative}
      />
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <BucketCount label="Positive" count={positive} accent="emerald" />
        <BucketCount label="Neutral" count={neutral} accent="zinc" />
        <BucketCount label="Negative" count={negative} accent="rose" />
      </div>
      {unscored > 0 && (
        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
          {scored} of {total} scored · {unscored} pending
        </p>
      )}
    </div>
  );
}

function formatScore(score: number): string {
  const sign = score > 0 ? "+" : "";
  return `${sign}${score.toFixed(3)}`;
}

function BucketBadge({
  bucket,
  mean,
}: {
  bucket: SentimentBucket;
  mean: number | null;
}) {
  if (mean === null) {
    return (
      <span className="rounded-full bg-zinc-100 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
        none
      </span>
    );
  }
  const palette = {
    positive: {
      pill: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
      Icon: ArrowUp,
    },
    neutral: {
      pill: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
      Icon: Minus,
    },
    negative: {
      pill: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
      Icon: ArrowDown,
    },
  }[bucket];
  const Icon = palette.Icon;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs font-semibold uppercase tracking-wide ${palette.pill}`}
    >
      <Icon className="h-3 w-3" />
      {bucket}
    </span>
  );
}

function DistributionBar({
  positive,
  neutral,
  negative,
}: {
  positive: number;
  neutral: number;
  negative: number;
}) {
  const total = positive + neutral + negative;
  if (total === 0) return null;
  const pct = (n: number) => (n / total) * 100;
  return (
    <div
      className="flex h-2 w-full overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"
      role="img"
      aria-label={`Sentiment distribution: ${positive} positive, ${neutral} neutral, ${negative} negative`}
    >
      {positive > 0 && (
        <div
          className="bg-emerald-500 dark:bg-emerald-400"
          style={{ width: `${pct(positive)}%` }}
        />
      )}
      {neutral > 0 && (
        <div
          className="bg-zinc-300 dark:bg-zinc-600"
          style={{ width: `${pct(neutral)}%` }}
        />
      )}
      {negative > 0 && (
        <div
          className="bg-rose-500 dark:bg-rose-400"
          style={{ width: `${pct(negative)}%` }}
        />
      )}
    </div>
  );
}

function BucketCount({
  label,
  count,
  accent,
}: {
  label: string;
  count: number;
  accent: "emerald" | "zinc" | "rose";
}) {
  const palette = {
    emerald: "text-emerald-700 dark:text-emerald-300",
    zinc: "text-zinc-600 dark:text-zinc-400",
    rose: "text-rose-700 dark:text-rose-300",
  }[accent];
  return (
    <div>
      <div className={`text-base font-semibold tabular-nums ${palette}`}>
        {count}
      </div>
      <div className="text-[11px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </div>
    </div>
  );
}
