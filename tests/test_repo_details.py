"""get_recipe_details: полная карточка (US3 + US4, карточка 3.1).

Шаги вставляются в тесте вперемешку - карточка обязана отдать их
по step_number. Статус НЕ фильтруется: pending нужен модерации (US8).
Связи назначаем по паттерну сида: add -> присваивание pending-объектам -> commit.
"""

from decimal import Decimal

from sqlalchemy import select

from db.models import Category, Diet, Ingredient, Recipe, RecipeIngredient, RecipeStep
from db.repo import IngredientAmount, get_recipe_details


async def test_full_card(session):
    # справочники выбираем ДО add(recipe): любой await между add и присваиванием
    # связей делает autoflush, рецепт получает identity - и присваивание
    # коллекции тянет ленивую загрузку (запрещено в async). Паттерн из db/seed.py
    breakfast = await session.scalar(select(Category).where(Category.name == "Завтрак"))
    keto = await session.scalar(select(Diet).where(Diet.name == "Кето"))

    recipe = Recipe(
        title="Овсянка с бананом",
        description="Быстрый сытный завтрак",
        cook_time_minutes=10,
        servings=1,
        calories_kcal=Decimal("320.50"),
        proteins_g=Decimal("10.20"),
        fats_g=Decimal("7.10"),
        carbs_g=Decimal("55.40"),
        status="approved",
    )
    session.add(recipe)  # с этого момента рецепт pending - связи без ленивых загрузок

    recipe.categories = [breakfast]
    recipe.diets = [keto]

    # шаги - НЕ по порядку номеров: карточка обязана отсортировать
    recipe.steps = [
        RecipeStep(step_number=2, instruction="Варить 5 минут"),
        RecipeStep(step_number=1, instruction="Залить молоком"),
        RecipeStep(step_number=3, instruction="Добавить банан"),
    ]

    oats = Ingredient(name="Овсяные хлопья")
    session.add(oats)
    session.add(
        RecipeIngredient(
            recipe=recipe,
            ingredient=oats,
            quantity=Decimal("50"),
            unit="г",
        )
    )
    await session.commit()

    card = await get_recipe_details(session, recipe.id)

    assert card.title == "Овсянка с бананом"
    assert card.description == "Быстрый сытный завтрак"
    assert card.calories_kcal == Decimal("320.50")
    assert card.proteins_g == Decimal("10.20")
    assert card.steps == ["Залить молоком", "Варить 5 минут", "Добавить банан"]
    assert card.ingredients == [IngredientAmount("Овсяные хлопья", Decimal("50"), "г")]
    assert card.categories == ["Завтрак"]
    assert card.diets == ["Кето"]
    assert card.rating_avg is None and card.reviews_count == 0  # спринт 3 (US6)


async def test_missing_recipe_returns_none(session):
    assert await get_recipe_details(session, recipe_id=999) is None


async def test_pending_recipe_is_visible_with_nullable_calories(session):
    """Карточка не фильтрует статус: очередь модерации смотрит pending (US8)."""
    recipe = Recipe(title="На модерации", status="pending", cook_time_minutes=20)
    session.add(recipe)
    await session.commit()

    card = await get_recipe_details(session, recipe.id)

    assert card is not None
    assert card.title == "На модерации"
    assert card.calories_kcal is None  # калорийность обязательна только до публикации
    assert card.steps == []  # рендер переживает пустые шаги/ингредиенты
    assert card.ingredients == []
