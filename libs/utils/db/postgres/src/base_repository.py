from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    def __init__(self, model, session: AsyncSession):
        self.model = model
        self.session = session

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def _touch_new(self, entity) -> None:
        now = self._utcnow()
        if (
            hasattr(entity, "created_at")
            and getattr(entity, "created_at", None) is None
        ):
            setattr(entity, "created_at", now)
        if hasattr(entity, "updated_at"):
            setattr(entity, "updated_at", now)

    def _touch_existing(self, entity) -> None:
        if hasattr(entity, "updated_at"):
            setattr(entity, "updated_at", self._utcnow())

    # CRUD
    async def add(self, entity, *, commit: bool = True, refresh: bool = True):
        self._touch_new(entity)
        self.session.add(entity)
        await self.session.flush()
        if commit:
            await self.session.commit()
            if refresh:
                await self.session.refresh(entity)
        return entity

    async def add_many(self, entities, *, commit: bool = True, refresh: bool = False):
        for entity in entities:
            self._touch_new(entity)
            self.session.add(entity)
        await self.session.flush()
        if commit:
            await self.session.commit()
            if refresh:
                for entity in entities:
                    await self.session.refresh(entity)
        return entities

    async def get(
        self,
        where: Optional[ColumnElement[bool] | Sequence[ColumnElement[bool]]] = None,
        options: Optional[Sequence[Any]] = None,
    ):
        stmt = select(self.model)
        if where is not None:
            if isinstance(where, (list, tuple)):
                for clause in where:
                    stmt = stmt.where(clause)
            else:
                stmt = stmt.where(where)

        if options:
            for opt in options:
                stmt = stmt.options(opt)

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, id_: Any):
        return await self.session.get(self.model, id_)

    async def get_all(self, *, offset: int = 0, limit: int = 100):
        stmt = select(self.model).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self,
        entity,
        data: Dict[str, Any],
        *,
        commit: bool = True,
        refresh: bool = True,
    ):
        for k, v in data.items():
            setattr(entity, k, v)
        self._touch_existing(entity)
        await self.session.flush()
        if commit:
            await self.session.commit()
            if refresh:
                await self.session.refresh(entity)
        return entity

    async def delete(self, entity, *, commit: bool = True) -> None:
        await self.session.delete(entity)
        if commit:
            await self.session.commit()
        return entity

    async def refresh(self, entity, *, attribute_names: Optional[Sequence[str]] = None):
        if attribute_names is not None:
            await self.session.refresh(entity, attribute_names=attribute_names)
        else:
            await self.session.refresh(entity)
        return entity

    async def commit(self):
        await self.session.commit()

    async def flush(self):
        await self.session.flush()

    async def select_one(self, where_clause, options: list = None):
        stmt = select(self.model).where(where_clause)
        if options:
            for opt in options:
                stmt = stmt.options(opt)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def select_many(self, where_clause, *, offset: int = 0, limit: int = 100):
        stmt = select(self.model).where(where_clause).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_ordered(
        self,
        *,
        where: Optional[ColumnElement[bool] | Sequence[ColumnElement[bool]]] = None,
        offset: int = 0,
        limit: int = 100,
        options: list = None,
        order_by: Any = None,
    ):
        stmt = select(self.model)

        # apply filters if provided
        if where is not None:
            if isinstance(where, (list, tuple)):
                for clause in where:
                    stmt = stmt.where(clause)
            else:
                stmt = stmt.where(where)

        if options:
            for opt in options:
                stmt = stmt.options(opt)

        if order_by is not None:
            stmt = stmt.order_by(order_by)

        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        where: Optional[ColumnElement[bool] | Sequence[ColumnElement[bool]]] = None,
    ) -> int:
        stmt = select(func.count()).select_from(self.model)

        if where is not None:
            if isinstance(where, (list, tuple)):
                for clause in where:
                    stmt = stmt.where(clause)
            else:
                stmt = stmt.where(where)

        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def execute(self, stmt, *, commit: bool = True):
        result = await self.session.execute(stmt)
        if commit:
            await self.session.commit()
        return result
