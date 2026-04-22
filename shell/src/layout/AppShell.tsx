import { Outlet } from "react-router-dom";
import { useAlertNotifier } from "../hooks/useAlertNotifier";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

export function AppShell() {
  useAlertNotifier();
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-white text-zinc-900 antialiased dark:bg-zinc-900 dark:text-zinc-100">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
