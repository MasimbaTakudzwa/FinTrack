import { Construction } from "lucide-react";

interface Props {
  title: string;
  milestone: string;
  description: string;
}

export function PagePlaceholder({ title, milestone, description }: Props) {
  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-8 dark:border-zinc-700 dark:bg-zinc-900/60">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
            <Construction className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
              {title}
            </h2>
            <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
              Sprint 3 · Milestone {milestone}
            </p>
          </div>
        </div>
        <p className="mt-4 text-sm text-zinc-600 dark:text-zinc-400">{description}</p>
      </div>
    </div>
  );
}
