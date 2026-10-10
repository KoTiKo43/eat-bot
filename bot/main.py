import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from handlers import router

# 1. Вставь свой токен!
BOT_TOKEN = "8638987333:AAHcWMETSMyViU4MKdaJ1FdS33VNqdttthM"

# API, которое выдает свежие списки бесплатных SOCKS5 прокси
PROXY_API_URL = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=5000&country=all"

async def fetch_proxies():
    """Скачивает список свежих прокси из интернета."""
    logging.info("Скачиваю список свежих прокси...")
    async with aiohttp.ClientSession() as session:
        async with session.get(PROXY_API_URL) as response:
            text = await response.text()
            # Разбиваем текст по строкам и убираем пустые
            proxies = [line.strip() for line in text.split('\n') if line.strip()]
            return proxies

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # 1. Получаем список прокси
    proxies = await fetch_proxies()
    logging.info(f"Найдено прокси: {len(proxies)} шт. Начинаю автоматический перебор...")

    working_session = None

    # 2. Перебираем каждый прокси
    for proxy in proxies:
        proxy_url = f"socks5://{proxy}"
        
        # Создаем сессию с текущим прокси
        session = AiohttpSession(proxy=proxy_url)
        bot = Bot(token=BOT_TOKEN, session=session)
        
        try:
            bot_info = await bot.get_me(request_timeout=5)
            logging.info(f"✅ УРА! Успешное подключение через {proxy_url}")
            logging.info(f"Бот {bot_info.first_name} готов к работе!")
            
            working_session = session
            break 
            
        except Exception:
            await session.close()
            logging.info(f"❌ {proxy_url} не отвечает, пробую следующий...")

    if not working_session:
        logging.error("Не удалось найти ни одного рабочего прокси из списка.")
        return

    bot = Bot(token=BOT_TOKEN, session=working_session)
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())