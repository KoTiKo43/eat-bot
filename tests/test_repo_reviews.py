"""Отзывы (US6, карточка 6.2): upsert, лента, своя оценка, рейтинг в списках."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from db.models import Recipe, Review, User
from db.repo import (
    add_favorite,
    find_recipes,
    get_recipe_details,
    get_recipe_reviews,
    get_user_review,
    list_favorites,
    upsert_review,
)


def _recipe(title: str) -> Recipe:
    return Recipe(
        title=title, status="approved", cook_time_minutes=15, calories_kcal=Decimal("300")
    )


async def _user(session, telegram_id, first_name=None, username=None) -> User:
    user = User(telegram_id=telegram_id, first_name=first_name, username=username)
    session.add(user)
    await session.commit()
    return user


async def test_upsert_creates_then_updates(session):
    alice = await _user(session, 1, first_name="Алиса")
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()

    await upsert_review(session, alice.id, oatmeal.id, rating=3, text="Норм")
    await upsert_review(session, alice.id, oatmeal.id, rating=5, text="Пересмотрел")

    count = await session.scalar(select(func.count()).select_from(Review))
    assert count == 1  # повтор = UPDATE, а не вторая строка: UNIQUE(recipe, user)
    mine = await get_user_review(session, alice.id, oatmeal.id)
    assert mine.rating == 5
    assert mine.text == "Пересмотрел"


async def test_upsert_replaces_review_entirely(session):
    """Замена целиком: text=None в повторном вызове очищает текст."""
    alice = await _user(session, 1, first_name="Алиса")
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()

    await upsert_review(session, alice.id, oatmeal.id, rating=4, text="Сытно")
    await upsert_review(session, alice.id, oatmeal.id, rating=5)  # без текста

    mine = await get_user_review(session, alice.id, oatmeal.id)
    assert mine.rating == 5
    assert mine.text is None  # старый текст не протёк в новую оценку


async def test_upsert_validates_rating(session):
    alice = await _user(session, 1)
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()

    with pytest.raises(ValueError):
        await upsert_review(session, alice.id, oatmeal.id, rating=0)
    with pytest.raises(ValueError):
        await upsert_review(session, alice.id, oatmeal.id, rating=6)


async def test_reviews_newest_first_and_author_names(session):
    alice = await _user(session, 1, first_name="Алиса")
    bob = await _user(session, 2, username="bob_no_name")
    incognito = await _user(session, 3)  # ни имени, ни username
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()

    await upsert_review(session, alice.id, oatmeal.id, rating=5, text="Вкусно")
    await upsert_review(session, bob.id, oatmeal.id, rating=4)
    await upsert_review(session, incognito.id, oatmeal.id, rating=1)

    reviews = await get_recipe_reviews(session, oatmeal.id)

    assert [r.rating for r in reviews] == [1, 4, 5]  # новые сверху
    assert reviews[0].author_name == "user3"  # fallback на заглушку
    assert reviews[1].author_name == "bob_no_name"  # имени нет - username
    assert reviews[2].author_name == "Алиса"


async def test_reviews_pagination(session):
    alice, bob, cat = (
        await _user(session, 1, first_name="А"),
        await _user(session, 2, first_name="Б"),
        await _user(session, 3, first_name="В"),
    )
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()
    for user in (alice, bob, cat):
        await upsert_review(session, user.id, oatmeal.id, rating=4)

    page1 = await get_recipe_reviews(session, oatmeal.id, limit=2)
    page2 = await get_recipe_reviews(session, oatmeal.id, limit=2, offset=2)

    assert len(page1) == 2  # список без has_more: "Дальше" = пришло ровно limit
    assert len(page2) == 1


async def test_rating_aggregated_in_catalog_and_card(session):
    alice = await _user(session, 1, first_name="А")
    bob = await _user(session, 2, first_name="Б")
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()
    await upsert_review(session, alice.id, oatmeal.id, rating=5)
    await upsert_review(session, bob.id, oatmeal.id, rating=3)

    page = await find_recipes(session, limit=10)
    assert page.items[0].rating_avg == 4.0
    assert page.items[0].reviews_count == 2

    card = await get_recipe_details(session, oatmeal.id)
    assert card.rating_avg == 4.0
    assert card.reviews_count == 2


async def test_rating_aggregated_in_favorites(session):
    """Тот же _recipe_page под JOIN избранного: корреляция подзапросов не сломалась."""
    alice = await _user(session, 1, first_name="А")
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()
    await add_favorite(session, alice.id, oatmeal.id)
    await upsert_review(session, alice.id, oatmeal.id, rating=5)

    page = await list_favorites(session, alice.id)

    assert page.items[0].rating_avg == 5.0
    assert page.items[0].reviews_count == 1


async def test_rating_is_none_without_reviews(session):
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()

    page = await find_recipes(session, limit=10)
    card = await get_recipe_details(session, oatmeal.id)

    assert page.items[0].rating_avg is None
    assert page.items[0].reviews_count == 0
    assert card.rating_avg is None
    assert card.reviews_count == 0


async def test_user_review_none_when_absent(session):
    alice = await _user(session, 1)
    oatmeal = _recipe("Овсянка")
    session.add(oatmeal)
    await session.commit()

    assert await get_user_review(session, alice.id, oatmeal.id) is None
