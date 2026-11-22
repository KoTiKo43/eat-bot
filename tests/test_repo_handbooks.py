"""Справочники каталога: list_* - имена для кнопок, get_* - id+имя для inline.

Фикстура session (conftest.py) заранее вставляет справочники, зеркаля
data-миграцию, - тесты проверяют ровно то, что увидит бот после deploy.
"""

from sqlalchemy import delete

from db.models import Category
from db.repo import get_categories, get_diets, list_categories, list_diets

CATEGORIES = ("Завтрак", "Обед", "Ужин", "Перекус")  # как в data-миграции
DIETS = ("Вегетарианское", "Веганское", "Без глютена", "Кето")


async def test_list_categories_names_for_buttons(session):
    names = await list_categories(session)

    assert names == list(CATEGORIES)  # порядок id из data-миграции, не алфавит


async def test_list_diets_names_for_buttons(session):
    names = await list_diets(session)

    assert names == list(DIETS)


async def test_get_categories_id_and_name(session):
    rows = await get_categories(session)

    assert [(c.id, c.name) for c in rows] == list(enumerate(CATEGORIES, start=1))


async def test_get_diets_id_and_name(session):
    rows = await get_diets(session)

    assert [(d.id, d.name) for d in rows] == list(enumerate(DIETS, start=1))


async def test_empty_handbook_is_empty_list(session):
    """Справочник пуст (забыли data-миграцию) - пустой список, а не ошибка."""
    await session.execute(delete(Category))
    await session.commit()

    assert await list_categories(session) == []
    assert await get_categories(session) == []
