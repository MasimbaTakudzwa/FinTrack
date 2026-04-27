#!/usr/bin/env node
// freeze-sidecar.mjs — invoked by Tauri's `beforeBundleCommand` (see
// shell/src-tauri/tauri.conf.json). Re-freezes the Python sidecar with
// PyInstaller right before Tauri copies `dist/fintrack-sidecar/` into the
// app bundle's Resources/ tree.
//
// Why this exists
// ---------------
// `tauri build` will happily bundle a stale or missing
// `dist/fintrack-sidecar/` directory — the bundler just copies whatever's at
// the configured resource path. That meant local builds (and any CI lane that
// forgot the explicit PyInstaller step) silently shipped binaries with old
// API routes. The user-visible failure is a 404 from a route that exists in
// source but not in the bundled freeze.
//
// Wiring this script into `beforeBundleCommand` makes PyInstaller a hard
// prerequisite of bundling — there's no path through `tauri build` that
// skips it. If PyInstaller isn't installed, or the freeze itself fails, the
// build aborts loudly instead of silently shipping stale code.
//
// Knobs
// -----
// FINTRACK_SKIP_FREEZE=1   Skip the PyInstaller run (useful in CI where the
//                          workflow runs PyInstaller explicitly first to
//                          fail fast before the Rust compile).

import { execSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { platform } from "node:process";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const isWindows = platform === "win32";

if (process.env.FINTRACK_SKIP_FREEZE === "1") {
  console.log("[freeze-sidecar] FINTRACK_SKIP_FREEZE=1 — skipping PyInstaller.");
  process.exit(0);
}

// Pick a Python interpreter. Prefer the repo-local venv (works without the
// user activating it manually) and fall back to PATH for CI / globally
// installed Python.
const venvPython = isWindows
  ? resolve(repoRoot, ".venv", "Scripts", "python.exe")
  : resolve(repoRoot, ".venv", "bin", "python");

const python = existsSync(venvPython) ? venvPython : isWindows ? "python.exe" : "python3";

console.log(`[freeze-sidecar] repo root: ${repoRoot}`);
console.log(`[freeze-sidecar] python:    ${python}`);
console.log(`[freeze-sidecar] running:   pyinstaller sidecar.spec --clean --noconfirm`);

const started = Date.now();
try {
  execSync(`"${python}" -m PyInstaller sidecar.spec --clean --noconfirm`, {
    stdio: "inherit",
    cwd: repoRoot,
  });
} catch (err) {
  console.error("[freeze-sidecar] PyInstaller failed — aborting bundle.");
  console.error(`[freeze-sidecar] hint: install packaging deps with`);
  console.error(`[freeze-sidecar]   pip install -r requirements.txt -r requirements-packaging.txt`);
  process.exit(typeof err.status === "number" ? err.status : 1);
}

// Sanity check — the bundler will copy whatever's at this path, so verify
// the freeze actually produced a binary and not an empty shell.
const bundleBinary = resolve(
  repoRoot,
  "dist",
  "fintrack-sidecar",
  isWindows ? "fintrack-sidecar.exe" : "fintrack-sidecar",
);
if (!existsSync(bundleBinary)) {
  console.error(`[freeze-sidecar] PyInstaller exited cleanly but ${bundleBinary} is missing.`);
  process.exit(2);
}
const sizeMb = (statSync(bundleBinary).size / (1024 * 1024)).toFixed(1);
const elapsed = ((Date.now() - started) / 1000).toFixed(1);
console.log(`[freeze-sidecar] ok — ${bundleBinary} (${sizeMb} MB) in ${elapsed}s`);
