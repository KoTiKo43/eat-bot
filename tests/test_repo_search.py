"""search_by_title: поиск без учёта регистра, кириллица (US2, карточка 2.2)."""

from db.models import Recipe
from db.repo import search_by_title


def _recipe(title: str, status: str = "approved") -> Recipe:
    return Recipe(title=title, status=status, cook_time_minutes=15)


async def test_search_is_case_insensitive_for_cyrillic(session):
    session.add_all([_recipe("Овсянка с бананом"), _recipe("Борщ классический")])
    await session.commit()

    page = await search_by_title(session, "ОВСЯН")

    assert [p.title for p in page.items] == ["Овсянка с бананом"]


async def test_search_skips_non_approved(session):
    session.add_all([_recipe("Овсянка обычная"), _recipe("Овсянка на модерации", "pending")])
    await session.commit()

    page = await search_by_title(session, "овсянка")

    assert [p.title for p in page.items] == ["Овсянка обычная"]


async def test_search_autoescapes_like_wildcards(session):
    session.add_all([_recipe("Овсянка"), _recipe("Борщ")])
    await session.commit()

    page = await search_by_title(session, "%")  # без autoescape матчился бы любой title

    assert page.items == []


async def test_search_no_match_returns_empty_page(session):
    session.add(_recipe("Борщ"))
    await session.commit()

    page = await search_by_title(session, "суши")

    assert page.items == []
    assert page.has_more is False


async def test_search_strips_spaces_and_empty_query_matches_all(session):
    session.add_all([_recipe("Борщ"), _recipe("Овсянка")])
    await session.commit()

    page = await search_by_title(session, "   ")

    assert len(page.items) == 2  # пустой запрос = вся витрина (боту лучше не звать)


async def test_search_pagination(session):
    session.add_all(_recipe(f"Борщ №{i}") for i in range(4))
    await session.commit()

    page = await search_by_title(session, "борщ", limit=3)

    assert len(page.items) == 3
    assert page.has_more is True
