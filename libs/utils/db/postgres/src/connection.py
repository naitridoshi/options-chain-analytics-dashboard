from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.declarative import DeclarativeMeta
from starlette_context import context
from starlette_context.errors import ContextDoesNotExistError

from libs.utils.config.src.postgres import POSTGRES_URI
from libs.utils.db.postgres.models.src.base import Base


class PostgresConnection:
    """
    Manages the connection to the PostgreSQL database.
    """

    def __init__(self, database_url, base: DeclarativeMeta):
        """
        Initialize the async connection manager with the database URL and Base metadata.

        :param database_url: The connection string for the PostgreSQL database
        :param base: The SQLAlchemy Base metadata for models
        """
        self.engine = create_async_engine(
            database_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self.SessionLocal = async_sessionmaker(self.engine, expire_on_commit=False)
        self.base = base

    @asynccontextmanager
    async def get_session(self):
        """
        Provide a new async database session.

        :return: A new SQLAlchemy AsyncSession
        """
        session_uuid = uuid4()
        context_available = True
        previous_session_id = None
        try:
            previous_session_id = context.get("sessionId")
            context["sessionId"] = str(session_uuid)
        except ContextDoesNotExistError:
            context_available = False

        async with self.SessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

        if context_available:
            try:
                if previous_session_id is None:
                    context["sessionId"] = None
                else:
                    context["sessionId"] = previous_session_id
            except ContextDoesNotExistError:
                pass

    async def close_engine(self):
        """
        Dispose of the async engine and close all connections.
        """
        await self.engine.dispose()


postgres_connection = PostgresConnection(database_url=POSTGRES_URI, base=Base)
