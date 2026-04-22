# PyInstaller spec for the FinTrack Python sidecar.
#
# Builds a one-folder bundle at ``dist/fintrack-sidecar/``:
#   fintrack-sidecar/
#     fintrack-sidecar        (or fintrack-sidecar.exe on Windows) — the binary
#     _internal/              — all Python deps + data files
#       alembic.ini           — bundled at root; migrations_runner._resource_base()
#                               picks this up via sys._MEIPASS
#       sidecar/db/migrations/
#         env.py              — alembic loads via exec(), so it MUST be a data file
#         script.py.mako
#         versions/0001_*.py  — migration scripts are also exec-loaded
#         versions/...
#       (plus every third-party wheel's .so/.pyd/.py/.pyi)
#
# One-folder over one-file because:
#   - No /tmp extraction cost on every cold launch (2-10s on macOS for this
#     set of deps — unacceptable for a user-facing app).
#   - Tauri bundles the whole directory as a resource; ``lib.rs`` spawns the
#     binary by path. Cleaner than the ``external_bin`` single-file flow
#     which would force us to either lose startup perf or pack everything
#     into a single executable.
#
# Build:
#   pip install -r requirements.txt -r requirements-packaging.txt
#   pyinstaller sidecar.spec --clean --noconfirm
#
# Output is deterministic at ``dist/fintrack-sidecar/``.

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ---------------------------------------------------------------------------
# Resolve paths relative to the spec file (SPECPATH is injected by PyInstaller)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(SPECPATH)
SIDECAR_DIR = REPO_ROOT / "sidecar"

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
# Alembic: ini file + migration scripts (env.py + versions/*.py are loaded via
# exec(), so PyInstaller's static analysis won't find them — they MUST be
# packaged as raw files).

datas: list[tuple[str, str]] = [
    (str(REPO_ROOT / "alembic.ini"), "."),
    # Preserve the dev-mode layout inside the bundle so
    # migrations_runner.MIGRATIONS_DIR resolves cleanly under sys._MEIPASS.
    (str(SIDECAR_DIR / "db" / "migrations"), "sidecar/db/migrations"),
]

# Third-party packages that ship data alongside their Python code.
# yfinance ships a pickled constants file; feedparser ships an encoding map;
# apscheduler has a plugin registry (setuptools entry_points) that needs to
# be preserved.
for pkg in ("yfinance", "feedparser", "apscheduler"):
    try:
        datas.extend(collect_data_files(pkg))
    except Exception:  # noqa: BLE001 - third-party pkg optional at collect time
        pass

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# Modules referenced by string — PyInstaller can't see them via static AST
# traversal. Grouped by reason:
#
# - uvicorn loops/protocols: selected at runtime based on what's installed.
# - APScheduler triggers/executors/jobstores: plugin-loaded via setuptools
#   entry_points; collect_submodules captures the whole tree.
# - SQLAlchemy SQLite dialect and alembic SQLite DDL: imported by DSN string.
# - Pydantic core / settings: wheel has C extensions PyInstaller needs to
#   stamp.

hiddenimports: list[str] = []

hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("apscheduler")
hiddenimports += collect_submodules("sqlalchemy.dialects")
hiddenimports += collect_submodules("alembic")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_core")
hiddenimports += collect_submodules("pydantic_settings")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("anyio")

# Our own models module — env.py references it via ``from sidecar.db import models``
# inside a function scope that PyInstaller may or may not trace. Explicit
# include keeps migrations reliable.
hiddenimports += [
    "sidecar.db.models",
    "sidecar.db.base",
    "sidecar.db.engine",
    "sidecar.config",
]

# ---------------------------------------------------------------------------
# Analysis + build
# ---------------------------------------------------------------------------

a = Analysis(
    [str(SIDECAR_DIR / "main.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Test libs that sneak in via transitive imports on some platforms.
        "pytest",
        "pytest_asyncio",
        "_pytest",
        # Tk isn't used and is massive on macOS.
        "tkinter",
        # Notebook tooling sometimes trailing off matplotlib/pandas:
        "IPython",
        "jupyter",
        "notebook",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# Windows gets ``.exe``; on POSIX the executable has no extension.
# Tauri's ``external_bin`` convention expects target-triple suffixes, but we
# ship via ``resources/`` and spawn by path — so a single canonical name
# (``fintrack-sidecar``) is fine and keeps the Rust side simple.
BINARY_NAME = "fintrack-sidecar"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=BINARY_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX often causes false-positive AV hits on Windows — not worth it.
    console=True,  # sidecar writes to stderr; a detached/windowed build would hide errors.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="fintrack-sidecar",
)
