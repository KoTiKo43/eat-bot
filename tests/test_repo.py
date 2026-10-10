"""Пин контракта repo: стабы живы, DTO на месте, engine/session работают."""

import pytest

from db import repo
from db.engine import make_engine, make_session_factory


@pytest.fixture
async def session():
    engine = make_engine("sqlite+aiosqlite://")
    factory = make_session_factory(engine)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_stub_recipe_details(session):
    details = await repo.get_recipe_details(session, recipe_id=1)
    missing = await repo.get_recipe_details(session, recipe_id=999)

    assert details is not None
    assert details.title == "Овсянка с бананом и корицей"
    assert details.steps and details.ingredients
    assert details.calories_kcal is not None
    assert missing is None


async def test_stub_catalog_and_reference_lists(session):
    page = await repo.find_recipes(session, repo.RecipeFilters(), limit=5)
    categories = await repo.list_categories(session)

    assert page.has_more is False
    assert len(page.items) == 3
    assert set(categories) == {"Завтрак", "Обед", "Ужин"}


async def test_stub_search_returns_page(session):
    page = await repo.search_by_title(session, query="овсян")

    assert isinstance(page, repo.RecipePage)
    assert page.items
