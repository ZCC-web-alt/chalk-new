from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import User


# 这里使用 pbkdf2_sha256，避免 bcrypt 在不同平台上的兼容性问题，
# 也不存在 72 字节长度限制，更适合含中文或较长密码的场景。
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def register_user(session: Session, username: str, password: str) -> Optional[User]:
    username = username.strip()
    if not username or not password:
        return None
    # 允许用户名重复，直接创建新用户
    # 如果需要后续区分，可根据 id 或创建时间处理。
    user = User(username=username, password_hash=hash_password(password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def authenticate_user(
    session: Session, username: str, password: str
) -> Optional[User]:
    username = username.strip()
    user = session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user

