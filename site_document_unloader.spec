from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ
from PyInstaller.building.datastruct import Tree
from PyInstaller.utils.hooks import collect_all


PROJECT_DIR = Path(SPECPATH).resolve().parent


def _collect_playwright_browsers() -> list[Tree]:
    browser_roots: list[Path] = []
    env_value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env_value and env_value != "0":
        browser_roots.append(Path(env_value).expanduser())

    try:
        import playwright

        playwright_dir = Path(playwright.__file__).resolve().parent
        browser_roots.append(playwright_dir / "driver" / "package" / ".local-browsers")
    except Exception:
        pass

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        browser_roots.append(Path(local_app_data) / "ms-playwright")

    browser_trees: list[Tree] = []
    seen: set[Path] = set()
    for root in browser_roots:
        if not root.exists():
            continue
        resolved_root = root.resolve()
        if resolved_root in seen:
            continue
        seen.add(resolved_root)
        browser_trees.append(Tree(str(resolved_root), prefix="ms-playwright"))

    return browser_trees


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
yaml_datas, yaml_binaries, yaml_hiddenimports = collect_all("yaml")

datas = [
    *playwright_datas,
    *yaml_datas,
    (str(PROJECT_DIR / "config.yaml"), "."),
    (str(PROJECT_DIR / "start.bat"), "."),
]

binaries = [
    *playwright_binaries,
    *yaml_binaries,
]

hiddenimports = sorted(
    set(
        [
            *playwright_hiddenimports,
            *yaml_hiddenimports,
        ]
    )
)

a = Analysis(
    ["site_document_unloader/__main__.py"],
    pathex=[str(PROJECT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_DIR / "pyinstaller_runtime_hook.py")],
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
    *_collect_playwright_browsers(),
    strip=False,
    upx=True,
    upx_exclude=[],
    name="site_document_unloader",
)
