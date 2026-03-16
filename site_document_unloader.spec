# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = []

datas += collect_data_files("playwright")
hiddenimports += collect_submodules("playwright")
hiddenimports += collect_submodules("yaml")
hiddenimports += collect_submodules("site_document_unloader")

config_example = project_root / "config.example.yaml"
start_bat = project_root / "start.bat"
runtime_hook = project_root / "pyinstaller_runtime_hook.py"
launcher_script = project_root / "run_site_document_unloader.py"

if config_example.exists():
    datas.append((str(config_example), "."))

if start_bat.exists():
    datas.append((str(start_bat), "."))

runtime_hooks = []
if runtime_hook.exists():
    runtime_hooks.append(str(runtime_hook))

a = Analysis(
    [str(launcher_script)],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
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
