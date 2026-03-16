# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(__file__).resolve().parent

datas = []
binaries = []
hiddenimports = []

# Playwright
datas += collect_data_files("playwright")
hiddenimports += collect_submodules("playwright")

# PyYAML
hiddenimports += collect_submodules("yaml")

# Optional files: НЕ требуем config.yaml
config_example = project_root / "config.example.yaml"
start_bat = project_root / "start.bat"

if config_example.exists():
    datas.append((str(config_example), "."))

if start_bat.exists():
    datas.append((str(start_bat), "."))

a = Analysis(
    [str(project_root / "site_document_unloader" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "pyinstaller_runtime_hook.py")],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="site_document_unloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="site_document_unloader",
)
