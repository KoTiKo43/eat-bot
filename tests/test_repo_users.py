"""get_or_create_user: разрешение telegram_id -> users.id для мидлвари (Ильхам)."""

from sqlalchemy import func, select

from db.models import User
from db.repo import get_or_create_user


async def test_first_call_creates_user(session):
    info = await get_or_create_user(
        session, telegram_id=100500, username="nit", first_name="Никита"
    )

    assert info.id is not None
    assert info.telegram_id == 100500
    assert info.username == "nit"
    assert info.first_name == "Никита"
    assert info.is_admin is False  # админ появится с ролями модерации (US8)


async def test_second_call_returns_same_user(session):
    first = await get_or_create_user(session, telegram_id=100500, username="old")
    second = await get_or_create_user(session, telegram_id=100500, username="new")

    assert second.id == first.id
    users_count = await session.scalar(select(func.count()).select_from(User))
    assert users_count == 1  # дублей нет: UNIQUE(telegram_id) + проверка в repo


async def test_profile_changes_are_updated(session):
    await get_or_create_user(session, telegram_id=100500, username="old_name", first_name="А")

    info = await get_or_create_user(
        session, telegram_id=100500, username="new_name", first_name="Б"
    )

    assert info.username == "new_name"  # Telegram-профиль меняется - тащим актуальный
    assert info.first_name == "Б"


async def test_different_telegram_ids_are_different_users(session):
    alice = await get_or_create_user(session, telegram_id=1, first_name="Алиса")
    bob = await get_or_create_user(session, telegram_id=2, first_name="Боб")

    assert alice.id != bob.id
