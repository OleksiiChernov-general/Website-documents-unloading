from __future__ import annotations

import os
import sys
from pathlib import Path


def _configure_playwright_browsers_path() -> None:
    if not getattr(sys, "frozen", False):
        return

    executable_dir = Path(sys.executable).resolve().parent
    candidates = [
        executable_dir / "ms-playwright",
        executable_dir / "_internal" / "ms-playwright",
    ]

    for candidate in candidates:
        if candidate.exists():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(candidate))
            return


_configure_playwright_browsers_path()
