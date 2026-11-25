"""Модерация (US8, карточка 8.2): очередь, одобрение, отклонение, публикации."""

from decimal import Decimal

import pytest

from db.models import Recipe, User
from db.repo import (
    NewRecipe,
    create_proposed_recipe,
    find_recipes,
    list_pending_recipes,
    moderate_recipe,
)


def _pending(title: str, calories: str | None = "300.00") -> Recipe:
    return Recipe(
        title=title,
        status="pending",
        cook_time_minutes=20,
        calories_kcal=Decimal(calories) if calories is not None else None,
    )


async def _moderator_id(session) -> int:
    user = User(telegram_id=1, first_name="Модератор", is_admin=True)
    session.add(user)
    await session.commit()
    return user.id


async def test_pending_queue_oldest_first(session):
    session.add_all([_pending("Первый"), _pending("Второй"), _pending("Третий")])
    session.add(Recipe(title="Витрина", status="approved", cook_time_minutes=10))
    await session.commit()

    page1 = await list_pending_recipes(session, limit=2)
    page2 = await list_pending_recipes(session, limit=2, offset=2)

    assert [p.title for p in page1.items] == ["Первый", "Второй"]  # старые сверху
    assert page1.has_more is True
    assert [p.title for p in page2.items] == ["Третий"]
    assert page2.has_more is False  # approved в очередь не попадает


async def test_approve_requires_calories_then_publishes(session):
    moderator_id = await _moderator_id(session)
    draft = _pending("Черновик", calories=None)
    session.add(draft)
    await session.commit()

    with pytest.raises(ValueError, match="калорийность"):
        await moderate_recipe(session, draft.id, moderator_id, approved=True)
    await session.refresh(draft)
    assert draft.status == "pending"  # отказ не поменял состояние

    draft.calories_kcal = Decimal("250.00")
    await session.commit()
    await moderate_recipe(session, draft.id, moderator_id, approved=True)

    await session.refresh(draft)
    assert draft.status == "approved"
    assert draft.moderated_by == moderator_id
    assert draft.moderated_at is not None
    assert [p.title for p in (await find_recipes(session, limit=10)).items] == ["Черновик"]


async def test_reject_without_calories(session):
    """Отклонить можно и без КБЖУ: правило только про публикацию."""
    moderator_id = await _moderator_id(session)
    draft = _pending("Пустой", calories=None)
    session.add(draft)
    await session.commit()

    await moderate_recipe(session, draft.id, moderator_id, approved=False)

    await session.refresh(draft)
    assert draft.status == "rejected"
    assert draft.moderated_by == moderator_id
    assert (await find_recipes(session, limit=10)).items == []  # в каталог не попал
    assert (await list_pending_recipes(session, limit=10)).items == []  # из очереди ушёл


async def test_moderate_missing_recipe_raises(session):
    moderator_id = await _moderator_id(session)

    with pytest.raises(ValueError):
        await moderate_recipe(session, recipe_id=999, moderator_id=moderator_id, approved=True)


async def test_full_path_proposal_to_catalog(session):
    """Сквозной сценарий US7 -> US8 -> US1: предложение -> одобрение -> витрина."""
    moderator_id = await _moderator_id(session)
    author = User(telegram_id=2, first_name="Автор")
    session.add(author)
    await session.commit()

    form = NewRecipe(
        title="Гречка с грибами",
        cook_time_minutes=25,
        calories_kcal=Decimal("350.00"),
        categories=["Обед"],
        steps=["Отварить гречку"],
    )
    recipe_id = await create_proposed_recipe(session, author.id, form)
    assert (await list_pending_recipes(session, limit=10)).items != []  # в очереди

    await moderate_recipe(session, recipe_id, moderator_id, approved=True)

    page = await find_recipes(session, limit=10)
    assert [p.title for p in page.items] == ["Гречка с грибами"]
