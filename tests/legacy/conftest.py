from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Register the legacy flat-module paths used inside the vendored science core.
import chalk_app.bootstrap  # noqa: E402,F401
from chalk_app.core import db as core_db  # noqa: E402

sys.modules.setdefault("db", core_db)
