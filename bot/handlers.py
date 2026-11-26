import random
import re
from contextlib import suppress
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from sqlalchemy.ext.asyncio import AsyncSession

from db.repo import (
    IngredientAmount,
    NewRecipe,
    RecipeDetails,
    RecipeFilters,
    RecipePage,
    UserInfo,
    add_favorite,
    create_proposed_recipe,
    find_recipes,
    get_categories,
    get_diets,
    get_recipe_details,
    get_recipe_reviews,
    get_user_review,
    is_favorite,
    list_categories,
    list_favorites,
    list_pending_recipes,
    moderate_recipe,
    remove_favorite,
    search_by_title,
    upsert_review,
)

router = Router()

PAGE_SIZE = 5

BTN_CATALOG = "📖 Каталог"
BTN_FAVORITES = "⭐ Избранное"
BTN_SEARCH = "🔍 Поиск"
BTN_RANDOM = "🎲 Случайный рецепт"
BTN_PROPOSE = "➕ Предложить рецепт"
BTN_BACK = "🔙 Главное меню"
BTN_SKIP = "⏭ Пропустить"

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_CATALOG), KeyboardButton(text=BTN_FAVORITES)],
        [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_RANDOM)],
        [KeyboardButton(text=BTN_PROPOSE)],
    ],
    resize_keyboard=True,
)

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_BACK)]],
    resize_keyboard=True,
)

skip_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_SKIP)],
        [KeyboardButton(text=BTN_BACK)],
    ],
    resize_keyboard=True,
)


class SearchState(StatesGroup):
    waiting_for_query = State()


class ReviewState(StatesGroup):
    waiting_for_text = State()


class AddRecipeState(StatesGroup):
    waiting_for_name = State()
    waiting_for_category = State()
    waiting_for_desc = State()
    waiting_for_calories = State()
    waiting_for_bju = State()
    waiting_for_photo = State()
    waiting_for_ingredients = State()
    waiting_for_steps = State()


def _fmt_dec(val: Decimal | None, suffix: str = "") -> str:
    if val is None:
        return "—"
    text = f"{val:.2f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}"


def _parse_ingredient(line: str) -> IngredientAmount:
    m = re.match(r"^(.+?)\s*[-—:]\s*(\d+(?:[.,]\d+)?)\s*(.*)$", line.strip())
    if m:
        name = m.group(1).strip()
        qty = Decimal(m.group(2).replace(",", "."))
        unit = m.group(3).strip() or None
        return IngredientAmount(name=name, quantity=qty, unit=unit)
    return IngredientAmount(name=line.strip(), quantity=None, unit=None)


async def _show_text_view(
    callback: CallbackQuery,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    msg = callback.message
    if not isinstance(msg, Message):
        return
    if msg.photo:
        with suppress(TelegramBadRequest):
            await msg.delete()
        await msg.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        with suppress(TelegramBadRequest):
            await msg.edit_text(text, parse_mode="HTML", reply_markup=kb)


async def _show_card_view(
    callback: CallbackQuery,
    text: str,
    kb: InlineKeyboardMarkup,
    image_file_id: str | None = None,
) -> None:
    msg = callback.message
    if not isinstance(msg, Message):
        return
    can_caption = bool(image_file_id) and len(text) <= 1024
    if can_caption and image_file_id:
        if msg.photo:
            with suppress(TelegramBadRequest):
                await msg.edit_caption(caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            with suppress(TelegramBadRequest):
                await msg.delete()
            await msg.answer_photo(
                photo=image_file_id, caption=text, parse_mode="HTML", reply_markup=kb
            )
    else:
        await _show_text_view(callback, text, kb)


def _build_page_kb(
    page: RecipePage,
    prefix: str,
    extra_arg: str,
    offset: int,
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in page.items:
        meta: list[str] = []
        if item.cook_time_minutes:
            meta.append(f"⏱ {item.cook_time_minutes}м")
        if item.calories_kcal is not None:
            meta.append(f"🔥 {_fmt_dec(item.calories_kcal)}")
        if item.rating_avg is not None:
            meta.append(f"⭐ {item.rating_avg:.1f}")
        suffix = f" ({', '.join(meta)})" if meta else ""
        btn_text = f"🍽 {item.title}{suffix}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"recipe:{item.id}:0:{prefix}:{extra_arg}:{offset}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if offset > 0:
        prev_off = max(0, offset - PAGE_SIZE)
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"{prefix}:{extra_arg}:{prev_off}",
            )
        )
    if page.has_more:
        next_off = offset + PAGE_SIZE
        nav_row.append(
            InlineKeyboardButton(
                text="Дальше ➡️",
                callback_data=f"{prefix}:{extra_arg}:{next_off}",
            )
        )
    if nav_row:
        rows.append(nav_row)

    if back_callback:
        rows.append([InlineKeyboardButton(text="🎛 Изменить фильтры", callback_data=back_callback)])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_recipe_card(details: RecipeDetails, servings: int | None = None) -> str:
    base_servings = details.servings or 1
    target_servings = servings if servings and servings > 0 else base_servings
    scale = Decimal(target_servings) / Decimal(base_servings)

    lines = [f"🍽 <b>{details.title.upper()}</b>"]

    badges: list[str] = []
    if details.rating_avg is not None:
        badges.append(f"⭐ <b>{details.rating_avg:.1f}</b> ({details.reviews_count})")
    if details.categories:
        badges.append(f"📂 {', '.join(details.categories)}")
    if details.diets:
        badges.append(f"🥗 {', '.join(details.diets)}")
    if badges:
        lines.append(" • ".join(badges))

    if details.description:
        lines.append(f"<blockquote>{details.description}</blockquote>")

    meta: list[str] = []
    if details.cook_time_minutes:
        meta.append(f"⏱ {details.cook_time_minutes} мин")
    if details.servings or servings:
        meta.append(f"👥 {target_servings} порц.")
    if meta:
        lines.append(" | ".join(meta))

    lines.append(
        f"🔥 <b>КБЖУ (100 г):</b> {_fmt_dec(details.calories_kcal)} ккал  |  "
        f"Б: {_fmt_dec(details.proteins_g)}  Ж: {_fmt_dec(details.fats_g)}  "
        f"У: {_fmt_dec(details.carbs_g)}"
    )

    if details.ingredients:
        lines.append("\n🛒 <b>Ингредиенты:</b>")
        for ing in details.ingredients:
            scaled_qty = ing.quantity * scale if ing.quantity is not None else None
            qty = _fmt_dec(scaled_qty) if scaled_qty is not None else ""
            unit = f" {ing.unit}" if ing.unit else ""
            amount = f" — <b>{qty}{unit}</b>" if qty else ""
            lines.append(f"  ▫️ {ing.name}{amount}")

    if details.steps:
        lines.append("\n👨‍🍳 <b>Приготовление:</b>")
        for idx, step in enumerate(details.steps, start=1):
            lines.append(f"  <b>{idx}.</b> {step}")

    return "\n".join(lines)


async def _render_filter_menu(
    session: AsyncSession, state: FSMContext
) -> tuple[str, InlineKeyboardMarkup]:
    data = await state.get_data()
    sel_cat: str | None = data.get("flt_cat")
    sel_diet: str | None = data.get("flt_diet")
    sel_cal: int | None = data.get("flt_cal")

    cats = await get_categories(session)
    diets = await get_diets(session)

    rows: list[list[InlineKeyboardButton]] = []

    cat_row: list[InlineKeyboardButton] = []
    for c in cats:
        mark = "✅ " if sel_cat == c.name else "🍽 "
        cat_row.append(
            InlineKeyboardButton(text=f"{mark}{c.name}", callback_data=f"fset:cat:{c.id}")
        )
    if cat_row:
        rows.append(cat_row)

    diet_row: list[InlineKeyboardButton] = []
    for d in diets:
        mark = "✅ " if sel_diet == d.name else "🥗 "
        diet_row.append(
            InlineKeyboardButton(text=f"{mark}{d.name}", callback_data=f"fset:diet:{d.id}")
        )
        if len(diet_row) == 2:
            rows.append(diet_row)
            diet_row = []
    if diet_row:
        rows.append(diet_row)

    cal_row: list[InlineKeyboardButton] = []
    for cal_limit in (300, 400, 600):
        mark = "✅ " if sel_cal == cal_limit else "🔥 "
        cal_row.append(
            InlineKeyboardButton(
                text=f"{mark}≤{cal_limit} ккал", callback_data=f"fset:cal:{cal_limit}"
            )
        )
    rows.append(cal_row)

    rows.append(
        [
            InlineKeyboardButton(text="🔄 Сбросить", callback_data="fset:reset:0"),
            InlineKeyboardButton(text="✨ Показать рецепты", callback_data="cat:show:0"),
        ]
    )

    summary = [
        "🎛 <b>Фильтры каталога</b>\n",
        f"📂 Категория: <b>{sel_cat or 'Любая'}</b>",
        f"🥗 Диета: <b>{sel_diet or 'Любая'}</b>",
        f"🔥 Калорийность: <b>{'до ' + str(sel_cal) + ' ккал' if sel_cal else 'Любая'}</b>",
        "\n<i>Комбинируй параметры кнопками ниже и жми «Показать рецепты»:</i>",
    ]
    return "\n".join(summary), InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Базовые команды и навигация
# ---------------------------------------------------------------------------


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = message.from_user.first_name if message.from_user else "друг"
    await message.answer(
        f"Привет, <b>{name}</b>! 👋\n"
        "Добро пожаловать в <b>Eat!</b> — твой личный кулинарный помощник 👨‍🍳\n\n"
        "<blockquote>Выбирай блюда по фильтрам и КБЖУ, сохраняй любимые в избранное, "
        "пересчитывай порции и делись своими рецептами!</blockquote>\n\n"
        "Выбирай раздел в меню ниже 👇",
        parse_mode="HTML",
        reply_markup=main_kb,
    )


@router.message(F.text == BTN_BACK)
async def action_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🏠 Вы вернулись в главное меню.",
        reply_markup=main_kb,
    )


# ---------------------------------------------------------------------------
# Каталог и фильтры (US1)
# ---------------------------------------------------------------------------


@router.message(F.text == BTN_CATALOG)
async def show_catalog_categories(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    text, kb = await _render_filter_menu(session, state)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "catalog:categories")
async def cb_catalog_categories(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    text, kb = await _render_filter_menu(session, state)
    await _show_text_view(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("fset:"))
async def cb_filter_set(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not callback.data:
        return
    _, kind, val_str = callback.data.split(":", 2)
    data = await state.get_data()

    if kind == "reset":
        await state.update_data(flt_cat=None, flt_diet=None, flt_cal=None)
    elif kind == "cat":
        cats = {c.id: c.name for c in await get_categories(session)}
        chosen = cats.get(int(val_str))
        await state.update_data(flt_cat=None if data.get("flt_cat") == chosen else chosen)
    elif kind == "diet":
        diets = {d.id: d.name for d in await get_diets(session)}
        chosen = diets.get(int(val_str))
        await state.update_data(flt_diet=None if data.get("flt_diet") == chosen else chosen)
    elif kind == "cal":
        cal_val = int(val_str)
        await state.update_data(flt_cal=None if data.get("flt_cal") == cal_val else cal_val)

    text, kb = await _render_filter_menu(session, state)
    await _show_text_view(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_catalog_page(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if not callback.data:
        return
    _, extra_arg, offset_str = callback.data.split(":", 2)
    offset = int(offset_str)

    data = await state.get_data()
    sel_cat: str | None = data.get("flt_cat")
    sel_diet: str | None = data.get("flt_diet")
    sel_cal: int | None = data.get("flt_cal")

    filters = RecipeFilters(
        category=sel_cat,
        diet=sel_diet,
        max_calories=Decimal(sel_cal) if sel_cal else None,
    )
    page = await find_recipes(session, filters=filters, limit=PAGE_SIZE, offset=offset)

    parts = [p for p in (sel_cat, sel_diet, f"≤{sel_cal} ккал" if sel_cal else None) if p]
    title = f"📖 <b>Каталог ({', '.join(parts)})</b>" if parts else "📖 <b>Все рецепты</b>"

    if not page.items:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎛 Изменить фильтры", callback_data="catalog:categories"
                    )
                ]
            ]
        )
        await _show_text_view(
            callback, f"{title}\n\nПо выбранным фильтрам рецептов не найдено 😔", kb
        )
        await callback.answer()
        return

    kb = _build_page_kb(
        page,
        prefix="cat",
        extra_arg=extra_arg,
        offset=offset,
        back_callback="catalog:categories",
    )
    await _show_text_view(callback, f"{title}\n<i>Выберите блюдо из списка:</i>", kb)
    await callback.answer()


# ---------------------------------------------------------------------------
# Случайный рецепт и клавиатура карточки
# ---------------------------------------------------------------------------


async def _build_recipe_kb(
    session: AsyncSession,
    db_user: UserInfo,
    details: RecipeDetails,
    servings: int,
    rest: str,
) -> InlineKeyboardMarkup:
    fav = await is_favorite(session, user_id=db_user.id, recipe_id=details.id)
    fav_text = "💔 Убрать из избранного" if fav else "⭐ В избранное"

    user_rev = await get_user_review(session, user_id=db_user.id, recipe_id=details.id)
    rate_text = f"✏️ Оценка ({user_rev.rating}★)" if user_rev else "⭐ Оценить"
    rev_text = f"💬 Отзывы ({details.reviews_count})"

    rows: list[list[InlineKeyboardButton]] = []

    has_qty = any(i.quantity is not None for i in details.ingredients)
    if has_qty:
        minus_val = max(1, servings - 1)
        plus_val = min(20, servings + 1)
        rows.append(
            [
                InlineKeyboardButton(
                    text="➖", callback_data=f"recipe:{details.id}:{minus_val}:{rest}"
                ),
                InlineKeyboardButton(text=f"🍽 {servings} порц.", callback_data="noop"),
                InlineKeyboardButton(
                    text="➕", callback_data=f"recipe:{details.id}:{plus_val}:{rest}"
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=fav_text, callback_data=f"toggle_fav:{details.id}:{servings}:{rest}"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text=rate_text, callback_data=f"rate_menu:{details.id}:{rest}"),
            InlineKeyboardButton(text=rev_text, callback_data=f"revs:{details.id}:{rest}"),
        ]
    )

    if rest.startswith("rnd:"):
        rows.append([InlineKeyboardButton(text="🎲 Другой рецепт", callback_data="rnd:next")])
    else:
        parts = rest.split(":", 2)
        src_prefix = parts[0] if len(parts) > 0 else "cat"
        src_arg = parts[1] if len(parts) > 1 else "show"
        src_offset = parts[2] if len(parts) > 2 else "0"
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔙 Назад к списку", callback_data=f"{src_prefix}:{src_arg}:{src_offset}"
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == BTN_RANDOM)
async def show_random_recipe(message: Message, session: AsyncSession, db_user: UserInfo) -> None:
    page = await find_recipes(session, limit=100, offset=0)
    if not page.items:
        await message.answer("В каталоге пока нет одобренных рецептов 😔")
        return
    chosen = random.choice(page.items)
    details = await get_recipe_details(session, recipe_id=chosen.id)
    if details is None:
        return
    servings = details.servings or 1
    text = _format_recipe_card(details, servings=servings)
    kb = await _build_recipe_kb(session, db_user, details, servings, "rnd:0:0")
    if details.image_file_id and len(text) <= 1024:
        await message.answer_photo(
            photo=details.image_file_id, caption=text, parse_mode="HTML", reply_markup=kb
        )
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "rnd:next")
async def cb_random_next(callback: CallbackQuery, session: AsyncSession, db_user: UserInfo) -> None:
    page = await find_recipes(session, limit=100, offset=0)
    if not page.items:
        await callback.answer("Рецептов нет", show_alert=True)
        return
    chosen = random.choice(page.items)
    await _show_recipe_view(callback, session, db_user, chosen.id, 0, "rnd:0:0")
    await callback.answer("🎲 Подобрал другой рецепт!")


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer("Нажимайте ➖ или ➕ для пересчёта порций")


# ---------------------------------------------------------------------------
# Избранное (US5)
# ---------------------------------------------------------------------------


@router.message(F.text == BTN_FAVORITES)
async def show_favorites(message: Message, session: AsyncSession, db_user: UserInfo) -> None:
    page = await list_favorites(session, user_id=db_user.id, limit=PAGE_SIZE, offset=0)
    if not page.items:
        await message.answer(
            "⭐ <b>Избранное пусто</b>\n\n"
            "<i>Открывайте рецепты в каталоге и жмите «⭐ В избранное», "
            "чтобы сохранить их здесь.</i>",
            parse_mode="HTML",
        )
        return
    kb = _build_page_kb(page, prefix="fav", extra_arg="list", offset=0)
    await message.answer("⭐ <b>Ваши избранные рецепты:</b>", parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("fav:"))
async def cb_favorites_page(
    callback: CallbackQuery, session: AsyncSession, db_user: UserInfo
) -> None:
    if not callback.data:
        return
    _, _, offset_str = callback.data.split(":", 2)
    offset = int(offset_str)
    page = await list_favorites(session, user_id=db_user.id, limit=PAGE_SIZE, offset=offset)
    if not page.items:
        await _show_text_view(callback, "⭐ <b>В избранном пока нет сохранённых рецептов.</b>")
        await callback.answer()
        return
    kb = _build_page_kb(page, prefix="fav", extra_arg="list", offset=offset)
    await _show_text_view(callback, "⭐ <b>Ваши избранные рецепты:</b>", kb)
    await callback.answer()


# ---------------------------------------------------------------------------
# Карточка рецепта (US3, US4)
# ---------------------------------------------------------------------------


async def _show_recipe_view(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: UserInfo,
    recipe_id: int,
    servings_arg: int,
    rest: str,
) -> None:
    details = await get_recipe_details(session, recipe_id=recipe_id)
    if details is None:
        await callback.answer("Рецепт не найден", show_alert=True)
        return

    servings = servings_arg if servings_arg > 0 else (details.servings or 1)
    text = _format_recipe_card(details, servings=servings)
    kb = await _build_recipe_kb(session, db_user, details, servings, rest)
    await _show_card_view(callback, text, kb, image_file_id=details.image_file_id)


@router.callback_query(F.data.startswith("recipe:"))
async def cb_recipe_details(
    callback: CallbackQuery, session: AsyncSession, db_user: UserInfo
) -> None:
    if not callback.data:
        return
    _, recipe_id_str, servings_str, rest = callback.data.split(":", 3)
    await _show_recipe_view(callback, session, db_user, int(recipe_id_str), int(servings_str), rest)
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_fav:"))
async def cb_toggle_favorite(
    callback: CallbackQuery, session: AsyncSession, db_user: UserInfo
) -> None:
    if not callback.data:
        return
    _, recipe_id_str, servings_str, rest = callback.data.split(":", 3)
    recipe_id = int(recipe_id_str)
    servings = int(servings_str)

    fav = await is_favorite(session, user_id=db_user.id, recipe_id=recipe_id)
    if fav:
        await remove_favorite(session, user_id=db_user.id, recipe_id=recipe_id)
        await callback.answer("💔 Удалено из избранного")
    else:
        await add_favorite(session, user_id=db_user.id, recipe_id=recipe_id)
        await callback.answer("⭐ Добавлено в избранное!")

    await _show_recipe_view(callback, session, db_user, recipe_id, servings, rest)


# ---------------------------------------------------------------------------
# Отзывы и рейтинги (US6)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("revs:"))
async def cb_show_reviews(callback: CallbackQuery, session: AsyncSession) -> None:
    if not callback.data:
        return
    _, recipe_id_str, rest = callback.data.split(":", 2)
    recipe_id = int(recipe_id_str)
    reviews = await get_recipe_reviews(session, recipe_id=recipe_id, limit=10, offset=0)

    if not reviews:
        text = "💬 <b>Отзывы</b>\n\n<i>Пока никто не оставил отзыв. Будьте первым!</i>"
    else:
        lines = ["💬 <b>Отзывы о рецепте:</b>\n"]
        for r in reviews:
            stars = "⭐" * r.rating
            date_str = r.created_at.strftime("%d.%m.%Y")
            comment = f"\n<blockquote>{r.text}</blockquote>" if r.text else ""
            lines.append(f"👤 <b>{r.author_name}</b> <i>({date_str})</i> — {stars}{comment}\n")
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Оставить отзыв", callback_data=f"rate_menu:{recipe_id}:{rest}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад к рецепту", callback_data=f"recipe:{recipe_id}:0:{rest}"
                )
            ],
        ]
    )
    await _show_text_view(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("rate_menu:"))
async def cb_rate_menu(callback: CallbackQuery) -> None:
    if not callback.data:
        return
    _, recipe_id_str, rest = callback.data.split(":", 2)
    recipe_id = int(recipe_id_str)

    star_row = [
        InlineKeyboardButton(text=f"{i} ⭐", callback_data=f"rate_set:{recipe_id}:{i}:{rest}")
        for i in range(1, 6)
    ]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            star_row,
            [
                InlineKeyboardButton(
                    text="🔙 Назад к рецепту", callback_data=f"recipe:{recipe_id}:0:{rest}"
                )
            ],
        ]
    )
    await _show_text_view(callback, "⭐ <b>Оцените рецепт от 1 до 5 звёзд:</b>", kb)
    await callback.answer()


@router.callback_query(F.data.startswith("rate_set:"))
async def cb_rate_set(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    _, recipe_id_str, rating_str, rest = callback.data.split(":", 3)
    await state.update_data(
        rev_recipe_id=int(recipe_id_str),
        rev_rating=int(rating_str),
        rev_rest=rest,
    )
    await state.set_state(ReviewState.waiting_for_text)
    await callback.message.answer(
        f"Ваша оценка: {'⭐' * int(rating_str)}\n\n"
        f"✍️ Напишите короткий комментарий или нажмите «{BTN_SKIP}»:",
        reply_markup=skip_kb,
    )
    await callback.answer()


@router.message(ReviewState.waiting_for_text)
async def process_review_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: UserInfo,
) -> None:
    data = await state.get_data()
    recipe_id: int = data["rev_recipe_id"]
    rating: int = data["rev_rating"]
    rest: str = data.get("rev_rest", "cat:show:0")

    raw_text = (message.text or "").strip()
    comment = None if raw_text == BTN_SKIP or not raw_text else raw_text

    await upsert_review(
        session,
        user_id=db_user.id,
        recipe_id=recipe_id,
        rating=rating,
        text=comment,
    )
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🍲 Вернуться к рецепту", callback_data=f"recipe:{recipe_id}:0:{rest}"
                )
            ]
        ]
    )
    await message.answer(
        "✅ <b>Спасибо! Ваш отзыв сохранён.</b>", parse_mode="HTML", reply_markup=main_kb
    )
    await message.answer("Можете вернуться к карточке блюда:", reply_markup=kb)


# ---------------------------------------------------------------------------
# Поиск (US2)
# ---------------------------------------------------------------------------


@router.message(F.text == BTN_SEARCH)
async def start_search(message: Message, state: FSMContext) -> None:
    await message.answer(
        "🔍 <b>Поиск рецепта</b>\n\nВведите название блюда (можно часть слова):",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(SearchState.waiting_for_query)


@router.message(SearchState.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext, session: AsyncSession) -> None:
    query = (message.text or "").strip()
    await state.clear()

    page = await search_by_title(session, query=query, limit=PAGE_SIZE, offset=0)
    if not page.items:
        await message.answer(
            f"🔍 По запросу «<b>{query}</b>» ничего не найдено 😔",
            parse_mode="HTML",
            reply_markup=main_kb,
        )
        return

    safe_query = query.replace(":", "")[:20]
    kb = _build_page_kb(page, prefix="srch", extra_arg=safe_query, offset=0)
    await message.answer("Готово!", reply_markup=main_kb)
    await message.answer(
        f"🔍 <b>Результаты поиска по запросу «{query}»:</b>",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("srch:"))
async def cb_search_page(callback: CallbackQuery, session: AsyncSession) -> None:
    if not callback.data:
        return
    _, query, offset_str = callback.data.split(":", 2)
    offset = int(offset_str)
    page = await search_by_title(session, query=query, limit=PAGE_SIZE, offset=offset)
    kb = _build_page_kb(page, prefix="srch", extra_arg=query, offset=offset)
    await _show_text_view(callback, f"🔍 <b>Результаты поиска по запросу «{query}»:</b>", kb)
    await callback.answer()


# ---------------------------------------------------------------------------
# Предложить рецепт (US7)
# ---------------------------------------------------------------------------


@router.message(F.text == BTN_PROPOSE)
async def add_recipe_start(message: Message, state: FSMContext) -> None:
    await message.answer(
        "👨‍🍳 <b>Новый рецепт (шаг 1/7)</b>\n\nВведите <b>название блюда</b>:",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AddRecipeState.waiting_for_name)


@router.message(AddRecipeState.waiting_for_name)
async def add_recipe_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.update_data(recipe_name=(message.text or "").strip())
    cats = await list_categories(session)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=c) for c in cats], [KeyboardButton(text=BTN_BACK)]],
        resize_keyboard=True,
    )
    await message.answer(
        "📂 <b>Шаг 2/7.</b> Выберите <b>категорию блюда</b>:",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await state.set_state(AddRecipeState.waiting_for_category)


@router.message(AddRecipeState.waiting_for_category)
async def add_recipe_category(message: Message, state: FSMContext, session: AsyncSession) -> None:
    cats = await list_categories(session)
    chosen = (message.text or "").strip()
    if chosen not in cats:
        await message.answer(f"Пожалуйста, выберите категорию из списка: {', '.join(cats)}")
        return
    await state.update_data(recipe_cat=chosen)
    await message.answer(
        "📝 <b>Шаг 3/7.</b> Напишите <b>краткое описание блюда</b>:",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AddRecipeState.waiting_for_desc)


@router.message(AddRecipeState.waiting_for_desc)
async def add_recipe_desc(message: Message, state: FSMContext) -> None:
    await state.update_data(recipe_desc=(message.text or "").strip())
    await message.answer(
        "🔥 <b>Шаг 4/7.</b> Введите <b>калорийность на 100 г</b> (например, <code>260</code>):",
        parse_mode="HTML",
    )
    await state.set_state(AddRecipeState.waiting_for_calories)


@router.message(AddRecipeState.waiting_for_calories)
async def add_recipe_calories(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        kcal = Decimal(raw)
        if kcal <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer(
            "⚠️ Введите положительное число (например, <code>260</code>):", parse_mode="HTML"
        )
        return
    await state.update_data(recipe_kcal=str(kcal))
    await message.answer(
        "⚖️ <b>Шаг 5/7.</b> Введите <b>БЖУ на 100 г</b> через пробел "
        "(например, <code>18 9 22</code>) или нажмите «⏭ Пропустить»:",
        parse_mode="HTML",
        reply_markup=skip_kb,
    )
    await state.set_state(AddRecipeState.waiting_for_bju)


@router.message(AddRecipeState.waiting_for_bju)
async def add_recipe_bju(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw == BTN_SKIP:
        await state.update_data(recipe_p=None, recipe_f=None, recipe_c=None)
    else:
        parts = re.split(r"[\s/,]+", raw)
        try:
            if len(parts) != 3:
                raise InvalidOperation
            p, f, c = (Decimal(x.replace(",", ".")) for x in parts)
            if p < 0 or f < 0 or c < 0:
                raise InvalidOperation
        except InvalidOperation:
            await message.answer(
                "⚠️ Введите 3 числа через пробел (например, <code>18 9 22</code>) "
                f"или нажмите «{BTN_SKIP}»:",
                parse_mode="HTML",
            )
            return
        await state.update_data(recipe_p=str(p), recipe_f=str(f), recipe_c=str(c))

    await message.answer(
        "📸 <b>Шаг 6/7.</b> Отправьте <b>фотографию блюда</b> (как фото со сжатием, не файлом) "
        f"или нажмите «{BTN_SKIP}»:",
        parse_mode="HTML",
        reply_markup=skip_kb,
    )
    await state.set_state(AddRecipeState.waiting_for_photo)


@router.message(AddRecipeState.waiting_for_photo)
async def add_recipe_photo(message: Message, state: FSMContext) -> None:
    if message.photo:
        file_id: str | None = message.photo[-1].file_id
    elif (message.text or "").strip() == BTN_SKIP:
        file_id = None
    else:
        await message.answer(
            f"⚠️ Отправьте именно сжатое фото (не файлом) или нажмите «{BTN_SKIP}»."
        )
        return

    await state.update_data(recipe_photo=file_id)
    await message.answer(
        "🛒 <b>Шаг 7/7 (ингредиенты).</b> Введите список, каждый с новой строки:\n"
        "<code>Творог - 200 г\nЯйцо - 2 шт</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AddRecipeState.waiting_for_ingredients)


@router.message(AddRecipeState.waiting_for_ingredients)
async def add_recipe_ingredients(message: Message, state: FSMContext) -> None:
    await state.update_data(recipe_ings=message.text or "")
    await message.answer(
        "👨‍🍳 <b>Последний шаг!</b> Введите <b>шаги приготовления</b> (каждый шаг с новой строки):",
        parse_mode="HTML",
    )
    await state.set_state(AddRecipeState.waiting_for_steps)


@router.message(AddRecipeState.waiting_for_steps)
async def add_recipe_steps(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: UserInfo,
) -> None:
    user_data = await state.get_data()
    recipe_name = user_data.get("recipe_name") or "Без названия"
    recipe_cat = user_data.get("recipe_cat") or "Обед"
    recipe_desc = user_data.get("recipe_desc") or ""
    recipe_kcal = Decimal(user_data.get("recipe_kcal", "0"))
    p_val = Decimal(user_data["recipe_p"]) if user_data.get("recipe_p") else None
    f_val = Decimal(user_data["recipe_f"]) if user_data.get("recipe_f") else None
    c_val = Decimal(user_data["recipe_c"]) if user_data.get("recipe_c") else None
    recipe_photo: str | None = user_data.get("recipe_photo")
    raw_ings = user_data.get("recipe_ings") or ""
    raw_steps = message.text or ""

    ings = [_parse_ingredient(line) for line in raw_ings.split("\n") if line.strip()]
    steps = [line.strip() for line in raw_steps.split("\n") if line.strip()]

    new_recipe = NewRecipe(
        title=recipe_name,
        description=recipe_desc,
        servings=2,
        calories_kcal=recipe_kcal,
        proteins_g=p_val,
        fats_g=f_val,
        carbs_g=c_val,
        image_file_id=recipe_photo,
        categories=[recipe_cat],
        ingredients=ings,
        steps=steps,
    )
    try:
        recipe_id = await create_proposed_recipe(session, author_id=db_user.id, data=new_recipe)
    except ValueError as exc:
        await message.answer(f"⚠️ Ошибка сохранения: {exc}", reply_markup=main_kb)
        await state.clear()
        return

    photo_note = "📸 с фото" if recipe_photo else "без фото"
    text = (
        f"✅ <b>Рецепт #{recipe_id} отправлен на модерацию!</b> ({photo_note})\n\n"
        f"🍽 <b>Название:</b> {recipe_name}\n"
        f"📂 <b>Категория:</b> {recipe_cat}\n"
        f"🔥 <b>КБЖУ:</b> {_fmt_dec(recipe_kcal)} ккал "
        f"(Б: {_fmt_dec(p_val)} / Ж: {_fmt_dec(f_val)} / У: {_fmt_dec(c_val)})\n"
        f"🛒 <b>Ингредиентов:</b> {len(ings)}  |  👨‍🍳 <b>Шагов:</b> {len(steps)}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_kb)
    await state.clear()


# ---------------------------------------------------------------------------
# Модерация (US8)
# ---------------------------------------------------------------------------


def _build_mod_queue_kb(page: RecipePage, offset: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"⏳ {item.title} ({_fmt_dec(item.calories_kcal)} ккал)",
                callback_data=f"mod_view:{item.id}:{offset}",
            )
        ]
        for item in page.items
    ]
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ Назад", callback_data=f"mod_list:{max(0, offset - PAGE_SIZE)}"
            )
        )
    if page.has_more:
        nav.append(
            InlineKeyboardButton(text="Дальше ➡️", callback_data=f"mod_list:{offset + PAGE_SIZE}")
        )
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("moderate"))
async def cmd_moderate(message: Message, session: AsyncSession, db_user: UserInfo) -> None:
    if not db_user.is_admin:
        await message.answer("⛔ Доступ к модерации есть только у администраторов.")
        return
    page = await list_pending_recipes(session, limit=PAGE_SIZE, offset=0)
    if not page.items:
        await message.answer("🛡 <b>Очередь модерации пуста.</b>", parse_mode="HTML")
        return
    await message.answer(
        "🛡 <b>Рецепты на модерации:</b>",
        parse_mode="HTML",
        reply_markup=_build_mod_queue_kb(page, offset=0),
    )


@router.callback_query(F.data.startswith("mod_list:"))
async def cb_mod_list(callback: CallbackQuery, session: AsyncSession, db_user: UserInfo) -> None:
    if not db_user.is_admin:
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    if not callback.data:
        return
    offset = int(callback.data.split(":", 1)[1])
    page = await list_pending_recipes(session, limit=PAGE_SIZE, offset=offset)
    if not page.items:
        await _show_text_view(callback, "🛡 <b>Очередь модерации пуста.</b>")
        await callback.answer()
        return
    await _show_text_view(
        callback, "🛡 <b>Рецепты на модерации:</b>", _build_mod_queue_kb(page, offset)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mod_view:"))
async def cb_mod_view(callback: CallbackQuery, session: AsyncSession, db_user: UserInfo) -> None:
    if not db_user.is_admin:
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    if not callback.data:
        return
    _, recipe_id_str, offset_str = callback.data.split(":", 2)
    recipe_id = int(recipe_id_str)
    details = await get_recipe_details(session, recipe_id=recipe_id)
    if details is None:
        await callback.answer("Рецепт не найден", show_alert=True)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить", callback_data=f"mod_act:{recipe_id}:approve:{offset_str}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"mod_act:{recipe_id}:reject:{offset_str}"
                ),
            ],
            [InlineKeyboardButton(text="🔙 К очереди", callback_data=f"mod_list:{offset_str}")],
        ]
    )
    text = f"🛡 <b>Заявка #{recipe_id} на модерацию:</b>\n\n{_format_recipe_card(details)}"
    await _show_card_view(callback, text, kb, image_file_id=details.image_file_id)
    await callback.answer()


@router.callback_query(F.data.startswith("mod_act:"))
async def cb_moderate_action(
    callback: CallbackQuery, session: AsyncSession, db_user: UserInfo
) -> None:
    if not db_user.is_admin:
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    if not callback.data:
        return
    _, recipe_id_str, decision, offset_str = callback.data.split(":", 3)
    approved = decision == "approve"
    try:
        await moderate_recipe(
            session,
            recipe_id=int(recipe_id_str),
            moderator_id=db_user.id,
            approved=approved,
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    status_text = (
        "✅ <b>Рецепт одобрен и опубликован в каталоге!</b>"
        if approved
        else "❌ <b>Рецепт отклонён.</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛡 К очереди модерации", callback_data=f"mod_list:{offset_str}"
                )
            ]
        ]
    )
    await _show_text_view(callback, status_text, kb)
    await callback.answer()
