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
# - yfinance ships a pickled constants file.
# - feedparser ships an encoding map.
# - apscheduler has a plugin registry (setuptools entry_points) that needs to
#   be preserved.
# - statsmodels ships reference tables (distribution critical values, seasonal
#   templates) under ``statsmodels/datasets/`` and ``statsmodels/stats/libqsturng``.
# - scipy ships .pyi stubs and compiled LAPACK routines that its submodules
#   reference by relative path at import time.
# - numpy's F2PY runtime shims live alongside its code.
# - pandas ships a timezone database clone + SQL dialect hooks.
# - patsy (statsmodels' formula engine) keeps builtin transforms under data.
# - vaderSentiment ships its lexicon (``vader_lexicon.txt``, ~30 KB) and a
#   small emoji-utf8 mapping under ``vaderSentiment/`` — without these, the
#   analyzer raises FileNotFoundError on first construction.
for pkg in (
    "yfinance",
    "feedparser",
    "apscheduler",
    "statsmodels",
    "scipy",
    "numpy",
    "pandas",
    "patsy",
    "vaderSentiment",
    # certifi ships the CA bundle (cacert.pem) — WITHOUT it every HTTPS call to
    # FRED / CoinGecko / Yahoo fails SSL verification *only in the frozen build*,
    # which the venv-run unit tests never catch.
    "certifi",
):
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
# requests' TLS stack: requests is statically traceable, but charset_normalizer
# loads its detection submodules dynamically and is easy to miss.
hiddenimports += collect_submodules("charset_normalizer")

# ML stack — statsmodels is lazily imported inside ``ml.forecast``, and its
# SARIMAX implementation string-loads ``statsmodels.tsa.statespace.*`` and
# ``statsmodels.tools.*`` at fit time. scipy/numpy/pandas/patsy all have
# plugin-style submodules loaded via entry_points or dynamic ``import_module``
# calls — collect_submodules recursively captures them so the frozen build
# doesn't die at training time with ``ModuleNotFoundError``.
hiddenimports += collect_submodules("statsmodels")
hiddenimports += collect_submodules("scipy")
hiddenimports += collect_submodules("numpy")
hiddenimports += collect_submodules("pandas")
hiddenimports += collect_submodules("patsy")

# vaderSentiment is lazy-imported inside ``ml.sentiment._get_analyzer`` so
# PyInstaller's static AST traversal misses it; pin both submodules so the
# frozen sidecar can score headlines.
hiddenimports += collect_submodules("vaderSentiment")

# Our own models module — env.py references it via ``from sidecar.db import models``
# inside a function scope that PyInstaller may or may not trace. Explicit
# include keeps migrations reliable. The ml.* modules likewise get lazy-
# imported from the scheduler / API routers, so we pin them here too.
hiddenimports += [
    "sidecar.db.models",
    "sidecar.db.base",
    "sidecar.db.engine",
    "sidecar.config",
    "ml.forecast",
    "ml.jobs",
    "ml.persistence",
    "ml.sentiment",
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
