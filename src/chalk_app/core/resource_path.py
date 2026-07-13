# -*- coding: utf-8 -*-
"""Path helpers for resources and runtime data."""

import os
import sys

from chalk_app.paths import PROJECT_ROOT


def resource_path(relative_path: str) -> str:
    """Return the absolute path for bundled/static resources."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = str(PROJECT_ROOT)
    return os.path.join(base, relative_path)


def runtime_path(relative_path: str = "") -> str:
    """Return the absolute path for runtime data such as DBs and logs."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = str(PROJECT_ROOT)
    if relative_path:
        return os.path.join(base, relative_path)
    return base
