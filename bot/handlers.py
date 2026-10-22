from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Каталог")],
            [KeyboardButton(text="Поиск"), KeyboardButton(text="Предложить рецепт")],
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "Привет! Я бот с рецептами Eat! 👨‍🍳\nВыбирай действие в меню ниже:", reply_markup=kb
    )


@router.message(F.text == "Каталог")
async def show_catalog(message: Message):
    fake_catalog = (
        "🍲 <b>Борщ домашний</b>\n<i>Категория: Супы</i>\n\n"
        "🍕 <b>Пицца Маргарита</b>\n<i>Категория: Выпечка</i>\n\n"
        "<i>*Здесь скоро будет вывод настоящих рецептов из БД*</i>"
    )
    await message.answer(fake_catalog, parse_mode="HTML")
