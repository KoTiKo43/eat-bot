"""Фабрики движка и сессий - единственная точка создания AsyncEngine.

SQLite не проверяет внешние ключи, пока на каждом соединении не включена
PRAGMA foreign_keys=ON. Без неё CASCADE/SET NULL/RESTRICT из схемы
молча не работают. На PostgreSQL огород не нужен - там FK всегда включены,
поэтому прагма вешается только для диалекта sqlite.
"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url)
    if engine.dialect.name == "sqlite":
        event.listens_for(engine.sync_engine, "connect")(_sqlite_pragma)
    return engine


def _sqlite_pragma(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: объекты можно читать после коммита -
    # хендлеры рендерят данные сразу после записи
    return async_sessionmaker(engine, expire_on_commit=False)
