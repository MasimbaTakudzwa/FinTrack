import { ExternalLink, Newspaper } from "lucide-react";
import { Link } from "react-router-dom";
import type { Article } from "../api/client";

type Density = "compact" | "comfortable";

interface NewsListProps {
  articles: Article[];
  loading: boolean;
  error: string | null;
  emptyMessage?: string;
  /** When set, the article's own symbol chips will hide this one (it's redundant in AssetDetail). */
  hideSymbol?: string;
  density?: Density;
}

/**
 * Renders a newest-first list of news articles with symbol chips.
 *
 * Used in two places:
 *  - AssetDetail right sidebar (compact, hides the current symbol)
 *  - /news standalone page (comfortable, shows all symbols)
 */
export function NewsList({
  articles,
  loading,
  error,
  emptyMessage = "No news yet.",
  hideSymbol,
  density = "comfortable",
}: NewsListProps) {
  if (loading && articles.length === 0) {
    return (
      <div className="flex items-center justify-center py-6 text-sm text-zinc-500 dark:text-zinc-400">
        Loading news…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
        {error}
      </div>
    );
  }

  if (articles.length === 0) {
    return (
      <div className="flex items-center gap-2 py-6 text-sm text-zinc-500 dark:text-zinc-400">
        <Newspaper className="h-4 w-4 text-zinc-400" />
        <span>{emptyMessage}</span>
      </div>
    );
  }

  return (
    <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
      {articles.map((a) => (
        <NewsRow
          key={a.id}
          article={a}
          hideSymbol={hideSymbol}
          density={density}
        />
      ))}
    </ul>
  );
}

function NewsRow({
  article,
  hideSymbol,
  density,
}: {
  article: Article;
  hideSymbol?: string;
  density: Density;
}) {
  const compact = density === "compact";
  const visibleSymbols = hideSymbol
    ? article.symbols.filter((s) => s.toUpperCase() !== hideSymbol.toUpperCase())
    : article.symbols;

  return (
    <li className={compact ? "py-2" : "py-3"}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <a
            href={article.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-start gap-1 text-sm font-medium text-zinc-900 hover:text-emerald-700 dark:text-zinc-100 dark:hover:text-emerald-400"
          >
            <span className="break-words">{article.headline}</span>
            <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-zinc-400" />
          </a>
          {!compact && article.summary && (
            <p className="mt-1 line-clamp-2 text-xs text-zinc-500 dark:text-zinc-400">
              {article.summary}
            </p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-zinc-500 dark:text-zinc-400">
            <span>{article.source}</span>
            <span>·</span>
            <span title={article.published_at}>
              {formatRelative(article.published_at)}
            </span>
            {visibleSymbols.length > 0 && (
              <>
                <span>·</span>
                <span className="flex flex-wrap gap-1">
                  {visibleSymbols.map((sym) => (
                    <Link
                      key={sym}
                      to={`/assets/${encodeURIComponent(sym)}`}
                      className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[10px] font-medium text-zinc-700 hover:bg-emerald-100 hover:text-emerald-800 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-emerald-900 dark:hover:text-emerald-300"
                    >
                      {sym}
                    </Link>
                  ))}
                </span>
              </>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}

function formatRelative(iso: string): string {
  // Backend stores naive UTC but the value here is ISO-8601; treat as UTC either way.
  const then = new Date(iso.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(iso) ? iso : iso + "Z");
  const diff = Date.now() - then.getTime();
  if (!Number.isFinite(diff) || diff < 0) {
    return then.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  }
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return then.toISOString().slice(0, 10);
}
