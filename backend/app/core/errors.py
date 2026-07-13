from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status


logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: Any | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def error_payload(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return payload


def validation_error_details(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for error in errors:
        details.append({
            "type": str(error.get("type") or "validation_error"),
            "loc": list(error.get("loc") or []),
            "msg": str(error.get("msg") or "Invalid value."),
        })
    return details


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        code = "HTTP_ERROR"
        message = "Request failed"
        details = None
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code", code))
            message = str(exc.detail.get("message", message))
            details = exc.detail.get("details")
        elif exc.detail:
            message = str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, message, details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_payload(
                "VALIDATION_ERROR",
                "Invalid request data.",
                validation_error_details(exc.errors()),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled API exception", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_payload(
                "INTERNAL_ERROR",
                "The server could not complete the request.",
            ),
        )
