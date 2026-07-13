from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import clear_session_cookie, current_user, get_db_session, set_session_cookie
from app.core.errors import ApiError
from app.core.legacy import auth, db
from app.schemas.auth import AuthInput, AuthResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(user) -> UserOut:
    return UserOut(id=user.id, username=user.username, createdAt=user.created_at)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: AuthInput, response: Response, session: Session = Depends(get_db_session)) -> AuthResponse:
    username = payload.username.strip()
    if not username:
        raise ApiError("VALIDATION_ERROR", "Username is required.", status.HTTP_422_UNPROCESSABLE_ENTITY)
    try:
        user = auth().register_user(session, username, payload.password)
    except IntegrityError:
        session.rollback()
        raise ApiError("USERNAME_TAKEN", "That username is already registered.", status.HTTP_409_CONFLICT)
    if user is None:
        raise ApiError("VALIDATION_ERROR", "Username and password are required.", status.HTTP_422_UNPROCESSABLE_ENTITY)
    set_session_cookie(response, user.id)
    return AuthResponse(user=_user_out(user))


@router.post("/login", response_model=AuthResponse)
def login(payload: AuthInput, response: Response, session: Session = Depends(get_db_session)) -> AuthResponse:
    user = auth().authenticate_user(session, payload.username, payload.password)
    if user is None:
        raise ApiError("INVALID_CREDENTIALS", "Username or password is incorrect.", status.HTTP_401_UNAUTHORIZED)
    set_session_cookie(response, user.id)
    return AuthResponse(user=_user_out(user))


@router.post("/logout")
def logout(
    response: Response,
    cookie_value: str | None = Cookie(default=None, alias=get_settings().session_cookie_name),
) -> dict[str, bool]:
    clear_session_cookie(response, cookie_value)
    return {"ok": True}


@router.get("/me", response_model=AuthResponse)
def me(user=Depends(current_user)) -> AuthResponse:
    return AuthResponse(user=_user_out(user))

