"""Пин контракта repo: публичный API и форма DTO не меняются молча.

Спринт 2 заменил стабы на настоящие запросы, поэтому пин стабов умер, 
а пин контракта остался: состав полей DTO и список публичных функций.
Поведение функций проверяют test_repo_*.py по карточкам.
"""

import dataclasses

from sqlalchemy.ext.asyncio import AsyncSession

from db import repo
from db.engine import make_engine, make_session_factory

# Полный состав полей каждой DTO контракта (спринт 1)
EXPECTED_DTO_FIELDS = {
    "RecipePreview": {
        "id",
        "title",
        "cook_time_minutes",
        "calories_kcal",
        "rating_avg",
        "reviews_count",
    },
    "RecipePage": {"items", "has_more"},
    "RecipeFilters": {"category", "diet", "min_calories", "max_calories"},
    "IngredientAmount": {"name", "quantity", "unit"},
    "RecipeDetails": {
        "id",
        "title",
        "description",
        "cook_time_minutes",
        "servings",
        "calories_kcal",
        "proteins_g",
        "fats_g",
        "carbs_g",
        "image_file_id",
        "categories",
        "diets",
        "ingredients",
        "steps",
        "rating_avg",
        "reviews_count",
    },
    "UserInfo": {"id", "telegram_id", "username", "first_name", "is_admin"},
    "ReviewEntry": {"author_name", "rating", "text", "created_at"},
    "NewRecipe": {
        "title",
        "description",
        "cook_time_minutes",
        "servings",
        "calories_kcal",
        "proteins_g",
        "fats_g",
        "carbs_g",
        "image_file_id",
        "categories",
        "diets",
        "ingredients",
        "steps",
    },
    "CategoryDTO": {"id", "name"},
}

# Публичные функции контракта: US1-US8, включая стабы спринта 3
PUBLIC_API = [
    "get_or_create_user",
    "list_categories",
    "list_diets",
    "get_categories",
    "get_diets",
    "find_recipes",
    "search_by_title",
    "get_recipe_details",
    "add_favorite",
    "remove_favorite",
    "list_favorites",
    "is_favorite",
    "upsert_review",
    "get_recipe_reviews",
    "get_user_review",
    "create_proposed_recipe",
    "list_pending_recipes",
    "moderate_recipe",
]


def test_dto_shapes_are_pinned():
    for name, expected in EXPECTED_DTO_FIELDS.items():
        dto = getattr(repo, name)
        actual = {f.name for f in dataclasses.fields(dto)}
        assert actual == expected, f"{name}: ожидали {expected}, получили {actual}"


def test_public_api_is_complete():
    for name in PUBLIC_API:
        assert callable(getattr(repo, name, None)), f"repo.{name} пропала из контракта"


async def test_engine_factories_work():
    engine = make_engine("sqlite+aiosqlite://")
    assert engine.dialect.name == "sqlite"
    factory = make_session_factory(engine)
    async with factory() as session:
        assert isinstance(session, AsyncSession)
    await engine.dispose()
