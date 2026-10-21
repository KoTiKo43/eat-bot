"""Тесты сида: загрузка, идемпотентность, валидация, файл демо-данных."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.engine import make_engine, make_session_factory
from db.models import (
    Base,
    Category,
    Diet,
    Ingredient,
    Recipe,
    RecipeIngredient,
)
from db.seed import DATA_PATH, SeedDataError, _load, seed

# Зеркалит значения из data-migration справочников
CATEGORIES = ("Завтрак", "Обед", "Ужин")
DIETS = ("Вегетарианское", "Веганское", "Без глютена", "Кето")


@pytest.fixture
async def session():
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


def _oatmeal() -> dict:
    return {
        "title": "Овсянка с бананом и корицей",
        "description": "Быстрый сытный завтрак",
        "cook_time_minutes": 10,
        "servings": 1,
        "calories_kcal": 320.5,
        "proteins_g": 10.2,
        "fats_g": 7.1,
        "carbs_g": 55.4,
        "categories": ["Завтрак"],
        "diets": ["Вегетарианское"],
        "ingredients": [
            {"name": "Овсяные хлопья", "quantity": 50, "unit": "г"},
            {"name": "Молоко", "quantity": 200, "unit": "мл"},
        ],
        "steps": ["Хлопья залить молоком", "Варить 5 минут"],
    }


async def test_seed_creates_approved_recipe_with_relations(session):
    added = await seed(session, [_oatmeal()])

    assert added == 1
    stmt = select(Recipe).options(
        selectinload(Recipe.steps),
        selectinload(Recipe.categories),
        selectinload(Recipe.ingredient_links).selectinload(RecipeIngredient.ingredient),
    )
    recipe = (await session.scalars(stmt)).one()

    assert recipe.status == "approved"
    assert recipe.author_id is None
    assert [step.step_number for step in recipe.steps] == [1, 2]
    assert [category.name for category in recipe.categories] == ["Завтрак"]

    link = recipe.ingredient_links[0]
    assert link.ingredient.name == "Овсяные хлопья"
    assert link.quantity == Decimal("50")
    assert link.unit == "г"


async def test_seed_is_idempotent(session):
    first = await seed(session, [_oatmeal()])
    second = await seed(session, [_oatmeal()])

    assert (first, second) == (1, 0)
    total = len((await session.scalars(select(Recipe.id))).all())
    assert total == 1


async def test_seed_rejects_unknown_reference(session):
    data = _oatmeal()
    data["categories"] = ["Полдник"]

    with pytest.raises(SeedDataError, match="Полдник"):
        await seed(session, [data])


async def test_seed_requires_macros(session):
    data = _oatmeal()
    del data["calories_kcal"]

    with pytest.raises(SeedDataError, match="calories_kcal"):
        await seed(session, [data])


async def test_shipped_seed_data_loads(session):
    data = _load(DATA_PATH)

    added = await seed(session, data)

    assert added == len(data) >= 3
    recipes_total = len((await session.scalars(select(Recipe.id))).all())
    ingredients_total = len((await session.scalars(select(Ingredient.id))).all())
    assert recipes_total == added
    assert ingredients_total >= 5
