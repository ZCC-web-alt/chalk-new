from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.legacy import bootstrap_legacy

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, object]:
    bootstrap_legacy()
    return {
        "status": "ok",
        "service": get_settings().app_name,
    }
