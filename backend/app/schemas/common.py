from __future__ import annotations

from math import ceil
from typing import Generic, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


class Pagination(BaseModel):
    page: int
    page_size: int = Field(alias="pageSize")
    total_items: int = Field(alias="totalItems")
    total_pages: int = Field(alias="totalPages")

    model_config = {"populate_by_name": True}


class Page(BaseModel, Generic[T]):
    data: list[T]
    pagination: Pagination


def paginate(
    items: list[T], page: int, page_size: int, *, total: int | None = None
) -> tuple[list[T], Pagination]:
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    total = len(items) if total is None else total
    start = (page - 1) * page_size
    return items[start : start + page_size], Pagination(
        page=page,
        pageSize=page_size,
        totalItems=total,
        totalPages=max(1, ceil(total / page_size)) if total else 0,
    )
