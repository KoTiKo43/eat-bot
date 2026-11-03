import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import TelegramObject, User
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.handlers import router
from db.engine import make_engine, make_session_factory
from db.repo import get_or_create_user

load_dotenv()


class DbSessionMiddleware(BaseMiddleware):
    """Создаёт AsyncSession на каждый апдейт и разрешает telegram_id -> UserInfo."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            tg_user: User | None = data.get("event_from_user")
            if tg_user is not None:
                data["db_user"] = await get_or_create_user(
                    session,
                    telegram_id=tg_user.id,
                    username=tg_user.username,
                    first_name=tg_user.first_name,
                    last_name=tg_user.last_name,
                )
            return await handler(event, data)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Не задан BOT_TOKEN в .env")

    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./eat.db")
    proxy_url = os.getenv("PROXY_URL") or None

    engine = make_engine(db_url)
    session_factory = make_session_factory(engine)

    session = AiohttpSession(proxy=proxy_url) if proxy_url else None
    bot = Bot(token=bot_token, session=session)

    dp = Dispatcher()
    dp.update.middleware(DbSessionMiddleware(session_factory))
    dp.include_router(router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Бот Eat! успешно запущен!")
        await dp.start_polling(bot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
