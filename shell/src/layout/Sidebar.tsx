import {
  Activity,
  Bell,
  Briefcase,
  GitCompare,
  LineChart,
  Newspaper,
  Settings as SettingsIcon,
  Star,
  TrendingUp,
} from "lucide-react";
import { NavLink } from "react-router-dom";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Activity;
  end?: boolean;
}

const ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LineChart, end: true },
  { to: "/watchlists", label: "Watchlists", icon: Star },
  { to: "/portfolio", label: "Portfolio", icon: Briefcase },
  { to: "/market", label: "Market", icon: TrendingUp },
  { to: "/compare", label: "Compare", icon: GitCompare },
  { to: "/news", label: "News", icon: Newspaper },
  { to: "/macro", label: "Macro", icon: Activity },
  { to: "/alerts", label: "Alerts", icon: Bell },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex h-14 items-center gap-2 border-b border-zinc-200 px-4 dark:border-zinc-800">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-emerald-500 to-sky-500 text-white">
          <LineChart className="h-4 w-4" />
        </div>
        <span className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
          FinTrack
        </span>
      </div>
      <nav className="flex-1 space-y-0.5 p-2">
        {ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              [
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                  : "text-zinc-700 hover:bg-zinc-200/60 dark:text-zinc-300 dark:hover:bg-zinc-800",
              ].join(" ")
            }
          >
            <Icon className="h-4 w-4" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-zinc-200 p-3 text-[11px] text-zinc-500 dark:border-zinc-800 dark:text-zinc-500">
        Local build — Phase 1
      </div>
    </aside>
  );
}
