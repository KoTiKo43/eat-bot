"""Схема консистентна: 11 таблиц в метаданных и все создаются в SQLite."""

from sqlalchemy.ext.asyncio import create_async_engine

from db.models import Base


async def test_metadata_has_all_tables():
    assert set(Base.metadata.tables) == {
        "users",
        "recipes",
        "recipe_steps",
        "categories",
        "diets",
        "ingredients",
        "recipe_categories",
        "recipe_diets",
        "recipe_ingredients",
        "favorites",
        "reviews",
    }


async def test_create_all_in_memory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
