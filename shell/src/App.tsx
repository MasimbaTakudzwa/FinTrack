import { useEffect } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { Alerts } from "./pages/Alerts";
import { AssetDetail } from "./pages/AssetDetail";
import { Compare } from "./pages/Compare";
import { Dashboard } from "./pages/Dashboard";
import { Macro } from "./pages/Macro";
import { Market } from "./pages/Market";
import { News } from "./pages/News";
import { Settings } from "./pages/Settings";
import { Watchlists } from "./pages/Watchlists";
import { applyTheme, resolveTheme, useSettings } from "./stores/useSettings";

function App() {
  const themeMode = useSettings((s) => s.theme);

  useEffect(() => {
    applyTheme(resolveTheme(themeMode));
    if (themeMode !== "system") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme(resolveTheme("system"));
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [themeMode]);

  return (
    <HashRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="assets/:symbol" element={<AssetDetail />} />
          <Route path="market" element={<Market />} />
          <Route path="compare" element={<Compare />} />
          <Route path="news" element={<News />} />
          <Route path="watchlists" element={<Watchlists />} />
          <Route path="macro" element={<Macro />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}

export default App;
