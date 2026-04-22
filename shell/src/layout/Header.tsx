import { useLocation } from "react-router-dom";
import { HealthIndicator } from "../components/HealthIndicator";
import { ThemeToggle } from "../components/ThemeToggle";

function titleForPath(pathname: string): string {
  if (pathname === "/") return "Dashboard";
  if (pathname.startsWith("/assets/")) {
    const sym = pathname.split("/")[2] ?? "";
    return sym ? `Asset — ${decodeURIComponent(sym)}` : "Asset";
  }
  if (pathname.startsWith("/market")) return "Market";
  if (pathname.startsWith("/macro")) return "Macro";
  if (pathname.startsWith("/settings")) return "Settings";
  return "FinTrack";
}

export function Header() {
  const { pathname } = useLocation();
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-200 bg-white/60 px-6 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/60">
      <h1 className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
        {titleForPath(pathname)}
      </h1>
      <div className="flex items-center gap-3">
        <HealthIndicator />
        <ThemeToggle />
      </div>
    </header>
  );
}
