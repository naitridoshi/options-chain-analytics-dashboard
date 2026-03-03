from abc import ABC
from typing import Any, Dict, Generic, List, Optional, Sequence, TypeVar

from sqlalchemy.sql.elements import ColumnElement

from libs.utils.db.postgres.src.base_repository import BaseRepository

T = TypeVar("T")


class BaseOperations(ABC, Generic[T]):
    """
    Abstract base class for PostgreSQL database operations.
    Provides common CRUD operations and query patterns.
    """

    def __init__(self, repository: BaseRepository):
        self._repository = repository

    @property
    def repository(self) -> BaseRepository:
        return self._repository

    # ----------------------------
    # READ OPERATIONS
    # ----------------------------

    async def find_by_id(
        self,
        entity_id: Any,
        *,
        include_deleted: bool = False,
    ) -> Optional[T]:
        entity = await self._repository.get_by_id(entity_id)

        if not entity:
            return None

        if not include_deleted and hasattr(entity, "is_deleted"):
            if getattr(entity, "is_deleted", False):
                return None

        return entity

    async def find_all(
        self,
        *,
        where: Optional[ColumnElement[bool] | Sequence[ColumnElement[bool]]] = None,
        offset: int = 0,
        limit: int = 100,
        order_by: Any = None,
        include_deleted: bool = False,
    ) -> List[T]:
        filters = []

        if where is not None:
            if isinstance(where, (list, tuple)):
                filters.extend(where)
            else:
                filters.append(where)

        if not include_deleted and hasattr(self.repository.model, "is_deleted"):
            filters.append(self.repository.model.is_deleted.is_(False))

        return await self._repository.list_ordered(
            where=filters,
            offset=offset,
            limit=limit,
            order_by=order_by,
        )

    async def count(
        self,
        where: Optional[ColumnElement[bool] | Sequence[ColumnElement[bool]]] = None,
        *,
        include_deleted: bool = False,
    ) -> int:
        filters = []

        if where is not None:
            if isinstance(where, (list, tuple)):
                filters.extend(where)
            else:
                filters.append(where)

        if not include_deleted and hasattr(self.repository.model, "is_deleted"):
            filters.append(self.repository.model.is_deleted.is_(False))

        return await self._repository.count(filters)

    async def exists(
        self,
        where: ColumnElement[bool],
        *,
        include_deleted: bool = False,
    ) -> bool:
        result = await self.find_all(
            where=where,
            limit=1,
            include_deleted=include_deleted,
        )
        return len(result) > 0

    # ----------------------------
    # CREATE
    # ----------------------------

    async def create(self, entity: T) -> T:
        return await self._repository.add(entity)

    async def create_many(self, entities: List[T]) -> List[T]:
        return await self._repository.add_many(entities)

    # ----------------------------
    # UPDATE
    # ----------------------------

    async def update_by_id(
        self,
        entity_id: Any,
        data: Dict[str, Any],
    ) -> Optional[T]:
        entity = await self._repository.get_by_id(entity_id)
        if not entity:
            return None

        return await self._repository.update(entity, data)

    # ----------------------------
    # DELETE (SOFT DELETE)
    # ----------------------------

    async def delete_by_id(
        self,
        entity_id: Any,
        *,
        hard_delete: bool = False,
    ) -> bool:
        entity = await self._repository.get_by_id(entity_id)
        if not entity:
            return False

        if hard_delete:
            await self._repository.delete(entity)
            return True

        if hasattr(entity, "is_deleted"):
            await self._repository.update(entity, {"is_deleted": True})
            return True

        # fallback to hard delete if no soft delete column
        await self._repository.delete(entity)
        return True
