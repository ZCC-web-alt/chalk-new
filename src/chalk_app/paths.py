"""Shared filesystem paths for the bundled Chalk scientific core."""

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PACKAGE_ROOT.parent
_SOURCE_PROJECT_ROOT = SRC_ROOT.parent if SRC_ROOT.name.lower() == "src" else Path.cwd()
PROJECT_ROOT = Path(os.environ.get("CHALK_PROJECT_ROOT", _SOURCE_PROJECT_ROOT)).expanduser().resolve()


def project_root() -> Path:
    return PROJECT_ROOT


def package_root() -> Path:
    return PACKAGE_ROOT
