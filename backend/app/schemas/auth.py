from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UserOut(BaseModel):
    id: int
    username: str
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class AuthInput(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class AuthResponse(BaseModel):
    user: UserOut

