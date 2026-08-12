# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Dam Monitoring Dashboard (Windows build).

Build from the repo root with:
    pyinstaller packaging/windows/app.spec

Must be run on Windows, with this project's requirements.txt (plus
pyinstaller and pyinstaller-hooks-contrib) installed into a Windows
Python -- Prophet/cmdstanpy ship a natively-compiled Stan binary per OS,
so a build made on Linux/macOS cannot run on Windows and vice versa.
"""

import os

import prophet
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# app.spec lives at <repo root>/packaging/windows/app.spec, so go up 3 levels.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(SPEC))))
PROPHET_DIR = os.path.dirname(prophet.__file__)

APP_NAME = "DamMonitoringDashboard"

datas = [
    # Prophet's precompiled Stan model + bundled cmdstan toolchain. Loaded
    # at runtime via importlib_resources.files("prophet") / "stan_model" /
    # ... (see prophet/models.py) -- this relative path must be preserved
    # exactly inside the frozen app.
    (os.path.join(PROPHET_DIR, "stan_model"), os.path.join("prophet", "stan_model")),
    # prophet/__init__.py reads this via a raw `open(__file__.parent /
    # "__version__.py")` rather than importing it, so PyInstaller's
    # collectors -- which treat .py files as import-only source -- never
    # pick it up as data on their own. Without it, `import prophet` raises
    # FileNotFoundError inside every single forecast fit.
    (os.path.join(PROPHET_DIR, "__version__.py"), "prophet"),
]
# Any other package data these ship (e.g. plotly's bundled plotly.min.js,
# used by our include_plotlyjs=True calls in gui.py).
datas += collect_data_files("prophet")
datas += collect_data_files("cmdstanpy")
datas += collect_data_files("plotly")

hiddenimports = []
hiddenimports += collect_submodules("prophet")
hiddenimports += collect_submodules("cmdstanpy")
hiddenimports += collect_submodules("plotly")

a = Analysis(
    [os.path.join(ROOT, "gui.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

# --onefile: everything (including QtWebEngine's Chromium bundle -- helper
# process, locales, ICU data -- and Prophet/cmdstan's binaries) is packed
# into this single .exe. At each launch the bootloader extracts it all to
# a fresh %TEMP%\_MEI... directory before the app actually starts, so
# startup is slower than the onedir layout and a large self-extracting
# .exe is more likely to trip antivirus heuristics. Chosen anyway per
# project requirement to ship one file instead of an exe + _internal/
# folder. Output is dist/DamMonitoringDashboard.exe directly (no COLLECT
# step / no dist/DamMonitoringDashboard/ folder).
