# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds a single windowed `Hyperion Account Manager.exe`.

Bundles the web UI, FastAPI/uvicorn, the pywebview desktop shell, and (best
effort) Playwright so username/password login works from the frozen exe. The
Chromium browser itself stays external (installed once via
`playwright install chromium`) — Playwright finds it in %LOCALAPPDATA%.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("hyperion_am/web", "hyperion_am/web")]
binaries = []
hiddenimports = ["win32crypt", "win32timezone", "clr"]

# Collect packages PyInstaller can't fully trace on its own.
for pkg in ("uvicorn", "webview", "playwright", "pythonnet", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("uvicorn")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Hyperion Account Manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
)
