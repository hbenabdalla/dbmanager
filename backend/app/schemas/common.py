from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=500)
    sort: str | None = None
    q: str | None = Field(None, max_length=200)


class Message(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


def apply_sort(stmt: Select[Any], sort: str | None, allowed: dict[str, Any], default: Any) -> Select[Any]:
    if not sort:
        return stmt.order_by(default)
    desc = sort.startswith("-")
    key = sort.lstrip("-+")
    col = allowed.get(key)
    if col is None:
        raise ValidationFailed(f"cannot sort by {key!r}", details={"allowed": sorted(allowed)})
    return stmt.order_by(col.desc().nulls_last() if desc else col.asc().nulls_last())


async def paginate(db: AsyncSession, stmt: Select[Any], page: int, page_size: int) -> tuple[list[Any], int]:
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(stmt.limit(page_size).offset((page - 1) * page_size))).scalars().unique().all()
    return list(rows), int(total)


def paginate_list(items: list[Any], page: int, page_size: int) -> tuple[list[Any], int]:
    start = (page - 1) * page_size
    return items[start : start + page_size], len(items)
