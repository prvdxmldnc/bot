import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from app.bot.handlers import router
from app.config import settings
from app.database import init_db


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db()
    redis = Redis.from_url(settings.redis_url)
    storage = RedisStorage(redis)
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
