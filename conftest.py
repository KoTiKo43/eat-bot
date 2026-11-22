"""Общие фикстуры тестов: чистая база в памяти, как у бота после миграций.

Фикстуру раньше держал каждый тест-файл у себя (test_repo.py, test_seed.py) -
новые файлы её не видели и падали с "fixture 'session' not found".
Теперь session живёт здесь, один раз, и доступна всем тестам.

Наполнение зеркалит боевую базу: 11 таблиц из метаданных + справочники из
data-миграции 381dc27f78d7_seed_reference_data.py. Если PO сменит состав
диет - поправить константы здесь И в миграции.
"""

import pytest

from db.engine import make_engine, make_session_factory
from db.models import Base, Category, Diet

# Зеркалит data-миграцию справочников 381dc27f78d7 (там 4 категории,
# включая "Перекус" - состав менять только вместе с миграцией)
CATEGORIES = ("Завтрак", "Обед", "Ужин", "Перекус")
DIETS = ("Вегетарианское", "Веганское", "Без глютена", "Кето")


@pytest.fixture
async def session():
    """Свежая in-memory база: все таблицы + справочники, PRAGMA foreign_keys=ON."""
    engine = make_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = make_session_factory(engine)
    async with factory() as session:
        session.add_all([Category(name=name) for name in CATEGORIES])
        session.add_all([Diet(name=name) for name in DIETS])
        await session.commit()
        yield session
    await engine.dispose()
