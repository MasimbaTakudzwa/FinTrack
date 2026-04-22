import { Monitor, Moon, Sun } from "lucide-react";
import { type ThemeMode, useSettings } from "../stores/useSettings";

const ORDER: ThemeMode[] = ["system", "light", "dark"];
const LABEL: Record<ThemeMode, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};

function Icon({ mode, className }: { mode: ThemeMode; className?: string }) {
  if (mode === "light") return <Sun className={className} />;
  if (mode === "dark") return <Moon className={className} />;
  return <Monitor className={className} />;
}

export function ThemeToggle() {
  const theme = useSettings((s) => s.theme);
  const setTheme = useSettings((s) => s.setTheme);
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      title={`Theme: ${LABEL[theme]} — click for ${LABEL[next]}`}
      className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
    >
      <Icon mode={theme} className="h-4 w-4" />
      <span>{LABEL[theme]}</span>
    </button>
  );
}
