from __future__ import annotations

from collections.abc import Iterator

from fastapi import Cookie, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.legacy import db
from app.core.security import session_store


def get_db_session() -> Iterator[Session]:
    session = db().get_session()
    try:
        yield session
    finally:
        session.close()


def current_user(
    session: Session = Depends(get_db_session),
    cookie_value: str | None = Cookie(default=None, alias=get_settings().session_cookie_name),
):
    user_id = session_store.get_user_id(cookie_value)
    if user_id is None:
        raise ApiError("UNAUTHENTICATED", "Please sign in to continue.", status.HTTP_401_UNAUTHORIZED)
    user = session.get(db().User, user_id)
    if user is None:
        raise ApiError("UNAUTHENTICATED", "The current session is no longer valid.", status.HTTP_401_UNAUTHORIZED)
    return user


def set_session_cookie(response: Response, user_id: int) -> None:
    settings = get_settings()
    token = session_store.create(user_id)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, token: str | None = None) -> None:
    settings = get_settings()
    session_store.delete(token)
    response.delete_cookie(settings.session_cookie_name, path="/")

