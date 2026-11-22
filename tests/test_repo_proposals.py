"""Предложения рецептов (US7, карточка 7.2): pending-рецепт одной транзакцией."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from db.models import Ingredient, Recipe, RecipeIngredient, User
from db.repo import IngredientAmount, NewRecipe, create_proposed_recipe, find_recipes


def _form(**overrides) -> NewRecipe:
    data = NewRecipe(
        title="Гречка с грибами",
        description="Просто и сытно",
        cook_time_minutes=25,
        servings=2,
        calories_kcal=Decimal("350.00"),
        proteins_g=Decimal("12.00"),
        fats_g=Decimal("8.00"),
        carbs_g=Decimal("60.00"),
        categories=["Обед"],
        diets=["Вегетарианское"],
        ingredients=[
            IngredientAmount("Гречка", Decimal("150"), "г"),
            IngredientAmount("Шампиньоны", Decimal("200"), "г"),
        ],
        steps=["Отварить гречку", "Обжарить грибы", "Смешать"],
    )
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


async def _author_id(session) -> int:
    user = User(telegram_id=100500, first_name="Автор")
    session.add(user)
    await session.commit()
    return user.id


async def _load(session, recipe_id: int) -> Recipe:
    """Перечитать рецепт из БД со всеми связями.

    Чтение коллекций у объекта из identity map тянет ленивую загрузку
    (запрещена в async), поэтому тест смотрит на базу, а не на память.
    """
    stmt = (
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.steps),
            selectinload(Recipe.categories),
            selectinload(Recipe.diets),
            selectinload(Recipe.ingredient_links).selectinload(RecipeIngredient.ingredient),
        )
    )
    return await session.scalar(stmt)


async def test_proposal_creates_pending_recipe_with_relations(session):
    author_id = await _author_id(session)

    recipe_id = await create_proposed_recipe(session, author_id, _form())

    recipe = await _load(session, recipe_id)
    assert recipe.status == "pending"  # решение за модератором (US8)
    assert recipe.author_id == author_id
    assert recipe.calories_kcal == Decimal("350.00")
    assert [s.instruction for s in recipe.steps] == [
        "Отварить гречку",
        "Обжарить грибы",
        "Смешать",
    ]
    assert [s.step_number for s in recipe.steps] == [1, 2, 3]  # нумерует repo
    assert [c.name for c in recipe.categories] == ["Обед"]
    assert [d.name for d in recipe.diets] == ["Вегетарианское"]
    amounts = sorted(
        (link.ingredient.name, link.quantity, link.unit) for link in recipe.ingredient_links
    )
    assert amounts == [
        ("Гречка", Decimal("150"), "г"),
        ("Шампиньоны", Decimal("200"), "г"),
    ]


async def test_proposal_stays_out_of_catalog(session):
    """Предложение не публикуется само: каталог показывает только approved (US1)."""
    author_id = await _author_id(session)
    await create_proposed_recipe(session, author_id, _form())

    page = await find_recipes(session, limit=10)

    assert page.items == []


async def test_proposal_reuses_existing_ingredient(session):
    """Get-or-create: справочник ингредиентов общий на все рецепты."""
    author_id = await _author_id(session)
    session.add(Ingredient(name="Гречка"))
    await session.commit()

    await create_proposed_recipe(session, author_id, _form())

    ingredients_count = await session.scalar(select(func.count()).select_from(Ingredient))
    assert ingredients_count == 2  # Гречка переиспользована, Шампиньоны созданы


async def test_proposal_rejects_unknown_references(session):
    author_id = await _author_id(session)

    with pytest.raises(ValueError, match="Десерты"):
        await create_proposed_recipe(session, author_id, _form(categories=["Десерты"]))
    with pytest.raises(ValueError, match="Сыроедение"):
        await create_proposed_recipe(session, author_id, _form(diets=["Сыроедение"]))

    recipes_count = await session.scalar(select(func.count()).select_from(Recipe))
    assert recipes_count == 0  # ошибки до add: в базе не осталось мусора


async def test_proposal_rejects_duplicate_ingredient(session):
    author_id = await _author_id(session)
    form = _form(
        ingredients=[
            IngredientAmount("Гречка", Decimal("150"), "г"),
            IngredientAmount("Гречка", Decimal("999"), "г"),
        ]
    )

    with pytest.raises(ValueError, match="Гречка"):
        await create_proposed_recipe(session, author_id, form)


async def test_proposal_minimal_form(session):
    """Минимальная форма валидна: один title, остальное опционально."""
    author_id = await _author_id(session)

    recipe_id = await create_proposed_recipe(
        session, author_id, NewRecipe(title="Просто название", cook_time_minutes=10)
    )

    recipe = await _load(session, recipe_id)
    assert recipe.status == "pending"
    assert recipe.steps == []
    assert recipe.categories == []
    assert recipe.ingredient_links == []
