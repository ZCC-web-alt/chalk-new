"""Import-path bootstrap for legacy flat imports after source reorganization."""

from __future__ import annotations

import sys
from pathlib import Path

from chalk_app.paths import PACKAGE_ROOT, PROJECT_ROOT, SRC_ROOT

_MODULE_DIRS = [
    SRC_ROOT,
    PACKAGE_ROOT,
    PACKAGE_ROOT / "core",
    PACKAGE_ROOT / "literature",
    PACKAGE_ROOT / "agents",
    PACKAGE_ROOT / "multimodal",
    PACKAGE_ROOT / "science",
    PACKAGE_ROOT / "reports",
    PACKAGE_ROOT / "app",
    PROJECT_ROOT,
]


def bootstrap() -> None:
    for path in reversed(_MODULE_DIRS):
        text = str(path)
        if Path(text).exists() and text not in sys.path:
            sys.path.insert(0, text)


bootstrap()

