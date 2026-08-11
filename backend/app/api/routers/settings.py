from __future__ import annotations

import os
import re
from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.dependencies import current_user
from app.core.errors import ApiError
from app.services import api_keys

router = APIRouter(prefix="/settings", tags=["settings"])


class ApiKeyInput(BaseModel):
    provider: Literal[
        "dashscope",
        "semantic_scholar",
        "ncbi",
        "ncbi_tool_email",
        "crossref_mailto",
        "nasa_ads",
        "materials_project",
    ] = "dashscope"
    api_key: str = Field(alias="apiKey", min_length=1, max_length=500)

    model_config = {"populate_by_name": True}


def _configured_credentials(user_id: int) -> dict[str, bool]:
    configured = api_keys.api_key_store.configured(user_id)
    environment = api_keys.api_key_store.science125_environment(user_id)
    configured["dashscope"] = configured["dashscope"] or bool(os.getenv("DASHSCOPE_API_KEY", "").strip())
    for provider, variable_names in api_keys.api_key_store.SCIENCE125_ENVIRONMENT_VARIABLES.items():
        configured[provider] = configured[provider] or any(
            bool(environment.get(variable_name, "").strip())
            for variable_name in variable_names
        )
    return configured


def _credential_status(user_id: int) -> dict[str, dict[str, bool]]:
    configured = _configured_credentials(user_id)
    return {
        "configured": configured,
        "effective": {
            "ncbi": configured["ncbi"] and configured["ncbi_tool_email"],
        },
    }


@router.get("/api-keys")
def get_api_key_status(user=Depends(current_user)) -> dict[str, dict[str, bool]]:
    return _credential_status(user.id)


@router.put("/api-keys")
def set_api_key(payload: ApiKeyInput, user=Depends(current_user)) -> dict[str, dict[str, bool]]:
    if get_settings().is_production:
        raise ApiError(
            "CREDENTIAL_STORAGE_DISABLED",
            "Production credentials must be injected through the server environment.",
            status.HTTP_403_FORBIDDEN,
        )
    value = payload.api_key.strip()
    if payload.provider == "ncbi_tool_email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ApiError(
            "INVALID_NCBI_TOOL_EMAIL",
            "Enter a valid contact email for NCBI E-utilities.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    api_keys.api_key_store.set_key(user.id, payload.provider, value)
    return _credential_status(user.id)
