"""Избранное (US5, карточка 5.2): идемпотентный add, тихий remove, новые сверху."""

from db.models import Recipe, User
from db.repo import add_favorite, is_favorite, list_favorites, remove_favorite


def _recipe(title: str) -> Recipe:
    return Recipe(title=title, status="approved", cook_time_minutes=15)


async def _user_id(session) -> int:
    user = User(telegram_id=100500)
    session.add(user)
    await session.commit()
    return user.id


async def test_favorite_full_cycle(session):
    user_id = await _user_id(session)
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()

    assert (await list_favorites(session, user_id)).items == []
    assert await is_favorite(session, user_id, oatmeal.id) is False

    await add_favorite(session, user_id, oatmeal.id)
    assert await is_favorite(session, user_id, oatmeal.id) is True

    page = await list_favorites(session, user_id)
    assert [p.title for p in page.items] == ["Овсянка"]

    assert await remove_favorite(session, user_id, oatmeal.id) is True
    assert await is_favorite(session, user_id, oatmeal.id) is False
    assert (await list_favorites(session, user_id)).items == []


async def test_add_favorite_is_idempotent(session):
    """Двойной клик по кнопке ☆ не роняет бота: повтора-строки не появляется."""
    user_id = await _user_id(session)
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()

    await add_favorite(session, user_id, oatmeal.id)
    await add_favorite(session, user_id, oatmeal.id)  # второй раз - тихо

    page = await list_favorites(session, user_id)
    assert len(page.items) == 1


async def test_remove_missing_favorite_returns_false(session):
    user_id = await _user_id(session)

    assert await remove_favorite(session, user_id, recipe_id=999) is False


async def test_favorites_newest_first(session):
    user_id = await _user_id(session)
    oatmeal, borsch, salad = _recipe("Овсянка"), _recipe("Борщ"), _recipe("Салат")
    session.add_all([oatmeal, borsch, salad])
    await session.commit()

    await add_favorite(session, user_id, oatmeal.id)  # добавили раньше всех
    await add_favorite(session, user_id, salad.id)  # добавили последним

    page = await list_favorites(session, user_id)

    assert [p.title for p in page.items] == ["Салат", "Овсянка"]  # новые сверху


async def test_favorites_pagination(session):
    user_id = await _user_id(session)
    recipes = [_recipe(f"Рецепт {i}") for i in range(3)]
    session.add_all(recipes)
    await session.commit()
    for recipe in recipes:
        await add_favorite(session, user_id, recipe.id)

    page1 = await list_favorites(session, user_id, limit=2)
    page2 = await list_favorites(session, user_id, limit=2, offset=2)

    assert len(page1.items) == 2 and page1.has_more is True
    assert len(page2.items) == 1 and page2.has_more is False


async def test_favorites_of_other_users_do_not_leak(session):
    alice_id = await _user_id(session)
    bob = User(telegram_id=200)
    session.add(bob)
    await session.commit()

    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()
    await add_favorite(session, alice_id, oatmeal.id)

    page = await list_favorites(session, bob.id)

    assert page.items == []  # чужое избранное не видно
