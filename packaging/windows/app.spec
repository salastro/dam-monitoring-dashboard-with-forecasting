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
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

# --onedir-equivalent layout (not --onefile): QtWebEngine ships a large
# Chromium bundle (helper process, locales, ICU data); onefile's
# extract-to-temp-dir-on-launch pattern is fragile for that and prone to
# antivirus false positives. This produces dist/DamMonitoringDashboard/
# as a folder you run the .exe from directly.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
