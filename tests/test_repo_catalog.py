"""find_recipes: витрина каталога (US1, карточка 1.4).

Главные правила: только approved, фильтры по именам из справочника
объединяются через AND, пагинация limit+1 -> has_more.
"""

from decimal import Decimal

from sqlalchemy import select

from db.models import Category, Diet, Recipe
from db.repo import RecipeFilters, find_recipes


def _recipe(title: str, status: str = "approved", calories: str | None = None) -> Recipe:
    return Recipe(
        title=title,
        status=status,
        calories_kcal=Decimal(calories) if calories is not None else None,
        cook_time_minutes=15,
    )


async def _category(session, name: str) -> Category:
    return await session.scalar(select(Category).where(Category.name == name))


async def _diet(session, name: str) -> Diet:
    return await session.scalar(select(Diet).where(Diet.name == name))


async def test_catalog_shows_only_approved(session):
    session.add_all(
        [
            _recipe("Борщ", "approved", "320.50"),
            _recipe("Черновик", "pending"),
            _recipe("Отклонёнка", "rejected"),
        ]
    )
    await session.commit()

    page = await find_recipes(session, limit=10)

    assert [p.title for p in page.items] == ["Борщ"]  # pending/rejected скрыты
    assert page.has_more is False


async def test_filter_by_category_name(session):
    breakfast = await _category(session, "Завтрак")
    oatmeal, borsch = _recipe("Овсянка"), _recipe("Борщ")
    session.add_all([oatmeal, borsch])
    # присваиваем, пока объекты pending: append после flush тянул бы
    # ленивую загрузку коллекции, а она запрещена в async (MissingGreenlet)
    oatmeal.categories = [breakfast]
    await session.commit()

    page = await find_recipes(session, RecipeFilters(category="Завтрак"), limit=10)

    assert [p.title for p in page.items] == ["Овсянка"]


async def test_filter_by_diet_name(session):
    keto = await _diet(session, "Кето")
    salad, oatmeal = _recipe("Салат с киноа"), _recipe("Овсянка")
    session.add_all([salad, oatmeal])
    salad.diets = [keto]
    await session.commit()

    page = await find_recipes(session, RecipeFilters(diet="Кето"), limit=10)

    assert [p.title for p in page.items] == ["Салат с киноа"]


async def test_category_and_diet_filters_combine_with_and(session):
    breakfast = await _category(session, "Завтрак")
    keto = await _diet(session, "Кето")
    oatmeal, salad = _recipe("Овсянка"), _recipe("Салат с киноа")
    session.add_all([oatmeal, salad])
    oatmeal.categories = [breakfast]
    salad.categories = [breakfast]
    salad.diets = [keto]
    await session.commit()

    page = await find_recipes(session, RecipeFilters(category="Завтрак", diet="Кето"), limit=10)

    assert [p.title for p in page.items] == ["Салат с киноа"]  # нужны ОБА условия


async def test_calories_range(session):
    session.add_all(
        [
            _recipe("Лёгкий салат", calories="150"),
            _recipe("Сытный борщ", calories="500"),
            _recipe("Без КБЖУ"),  # NULL не попадает ни в один диапазон
        ]
    )
    await session.commit()

    page = await find_recipes(
        session,
        RecipeFilters(min_calories=Decimal("200"), max_calories=Decimal("600")),
        limit=10,
    )

    assert [p.title for p in page.items] == ["Сытный борщ"]


async def test_pagination_has_more_and_offset(session):
    session.add_all(_recipe(f"Рецепт {i}", calories="100") for i in range(7))
    await session.commit()

    page1 = await find_recipes(session, limit=3)
    page2 = await find_recipes(session, limit=3, offset=3)
    page3 = await find_recipes(session, limit=3, offset=6)

    assert len(page1.items) == 3 and page1.has_more is True
    assert len(page2.items) == 3 and page2.has_more is True
    assert len(page3.items) == 1 and page3.has_more is False
    # стабильный order_by(id): страницы не пересекаются и не пропускают
    assert page1.items[-1].id < page2.items[0].id < page2.items[-1].id < page3.items[0].id


async def test_offset_beyond_end_returns_empty_page(session):
    session.add(_recipe("Овсянка", calories="100"))
    await session.commit()

    page = await find_recipes(session, limit=5, offset=100)

    assert page.items == []  # пустая страница, а не None и не ошибка
    assert page.has_more is False
