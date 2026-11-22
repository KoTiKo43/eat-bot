from decimal import Decimal, InvalidOperation

from aiogram import F, Router
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
    is_favorite,
    list_categories,
    list_favorites,
    list_pending_recipes,
    moderate_recipe,
    remove_favorite,
    search_by_title,
)

router = Router()

PAGE_SIZE = 5

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Каталог"), KeyboardButton(text="Избранное")],
        [KeyboardButton(text="Поиск"), KeyboardButton(text="Предложить рецепт")],
    ],
    resize_keyboard=True,
)

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Назад")]],
    resize_keyboard=True,
)


class SearchState(StatesGroup):
    waiting_for_query = State()


class AddRecipeState(StatesGroup):
    waiting_for_name = State()
    waiting_for_category = State()
    waiting_for_desc = State()
    waiting_for_calories = State()
    waiting_for_ingredients = State()
    waiting_for_steps = State()


def _fmt_dec(val: Decimal | None, suffix: str = "") -> str:
    if val is None:
        return "—"
    text = f"{val:.2f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}"


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
            meta.append(f"⏱ {item.cook_time_minutes} мин")
        if item.calories_kcal is not None:
            meta.append(f"🔥 {_fmt_dec(item.calories_kcal)} ккал")
        suffix = f" ({', '.join(meta)})" if meta else ""
        btn_text = f"🍽 {item.title}{suffix}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"recipe:{item.id}:{prefix}:{extra_arg}:{offset}",
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


def _format_recipe_card(details: RecipeDetails) -> str:
    lines = [f"🍲 <b>{details.title}</b>"]
    if details.categories:
        lines.append(f"<i>Категория: {', '.join(details.categories)}</i>")
    if details.diets:
        lines.append(f"<i>Диета: {', '.join(details.diets)}</i>")
    if details.description:
        lines.append(f"\n{details.description}")

    meta: list[str] = []
    if details.cook_time_minutes:
        meta.append(f"⏱ Время: {details.cook_time_minutes} мин")
    if details.servings:
        meta.append(f"🍽 Порций: {details.servings}")
    if meta:
        lines.append("\n" + " | ".join(meta))

    bju = (
        f"🔥 <b>КБЖУ на 100 г:</b> {_fmt_dec(details.calories_kcal)} ккал "
        f"(Б: {_fmt_dec(details.proteins_g)} г, "
        f"Ж: {_fmt_dec(details.fats_g)} г, "
        f"У: {_fmt_dec(details.carbs_g)} г)"
    )
    lines.append(bju)

    if details.ingredients:
        lines.append("\n<b>Ингредиенты:</b>")
        for ing in details.ingredients:
            qty = _fmt_dec(ing.quantity) if ing.quantity is not None else ""
            unit = f" {ing.unit}" if ing.unit else ""
            amount = f" — {qty}{unit}" if qty else ""
            lines.append(f"• {ing.name}{amount}")

    if details.steps:
        lines.append("\n<b>Пошаговое приготовление:</b>")
        for idx, step in enumerate(details.steps, start=1):
            lines.append(f"{idx}. {step}")

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
        mark = "✅ " if sel_cat == c.name else ""
        cat_row.append(
            InlineKeyboardButton(text=f"{mark}{c.name}", callback_data=f"fset:cat:{c.id}")
        )
    if cat_row:
        rows.append(cat_row)

    diet_row: list[InlineKeyboardButton] = []
    for d in diets:
        mark = "✅ " if sel_diet == d.name else ""
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
        mark = "✅ " if sel_cal == cal_limit else ""
        cal_row.append(
            InlineKeyboardButton(
                text=f"{mark}До {cal_limit} ккал", callback_data=f"fset:cal:{cal_limit}"
            )
        )
    rows.append(cal_row)

    rows.append(
        [
            InlineKeyboardButton(text="🔄 Сбросить", callback_data="fset:reset:0"),
            InlineKeyboardButton(text="🔍 Показать рецепты", callback_data="cat:show:0"),
        ]
    )

    summary = [
        "🎛 <b>Фильтры каталога</b>",
        f"• Категория: <b>{sel_cat or 'Любая'}</b>",
        f"• Диета: <b>{sel_diet or 'Любая'}</b>",
        f"• Калорийность: <b>{'до ' + str(sel_cal) + ' ккал' if sel_cal else 'Любая'}</b>",
        "\nНажимайте на кнопки ниже для выбора фильтров:",
    ]
    return "\n".join(summary), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Я бот с рецептами Eat! 👨‍🍳\nВыбирай действие в меню ниже:",
        reply_markup=main_kb,
    )


@router.message(F.text == "🔙 Назад")
async def action_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Действие отменено. Вы вернулись в главное меню.",
        reply_markup=main_kb,
    )


@router.message(F.text == "Каталог")
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
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("fset:"))
async def cb_filter_set(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not callback.data or not callback.message:
        return
    _, kind, val_str = callback.data.split(":", 2)
    data = await state.get_data()

    if kind == "reset":
        await state.update_data(flt_cat=None, flt_diet=None, flt_cal=None)
    elif kind == "cat":
        cats = {c.id: c.name for c in await get_categories(session)}
        chosen = cats.get(int(val_str))
        new_val = None if data.get("flt_cat") == chosen else chosen
        await state.update_data(flt_cat=new_val)
    elif kind == "diet":
        diets = {d.id: d.name for d in await get_diets(session)}
        chosen = diets.get(int(val_str))
        new_val = None if data.get("flt_diet") == chosen else chosen
        await state.update_data(flt_diet=new_val)
    elif kind == "cal":
        cal_val = int(val_str)
        new_val = None if data.get("flt_cal") == cal_val else cal_val
        await state.update_data(flt_cal=new_val)

    text, kb = await _render_filter_menu(session, state)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_catalog_page(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if not callback.data or not callback.message:
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
    title = f"<b>Каталог ({', '.join(parts)})</b>" if parts else "<b>Все рецепты</b>"

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
        await callback.message.edit_text(
            f"{title}\n\nПо выбранным фильтрам рецептов не найдено 😔",
            parse_mode="HTML",
            reply_markup=kb,
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
    await callback.message.edit_text(
        f"{title}\nВыберите рецепт:", parse_mode="HTML", reply_markup=kb
    )
    await callback.answer()


@router.message(F.text == "Избранное")
async def show_favorites(message: Message, session: AsyncSession, db_user: UserInfo) -> None:
    page = await list_favorites(session, user_id=db_user.id, limit=PAGE_SIZE, offset=0)
    if not page.items:
        await message.answer("В избранном пока нет сохранённых рецептов ⭐")
        return
    kb = _build_page_kb(page, prefix="fav", extra_arg="list", offset=0)
    await message.answer("⭐ <b>Ваши избранные рецепты:</b>", parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("fav:"))
async def cb_favorites_page(
    callback: CallbackQuery, session: AsyncSession, db_user: UserInfo
) -> None:
    if not callback.data or not callback.message:
        return
    _, _, offset_str = callback.data.split(":", 2)
    offset = int(offset_str)
    page = await list_favorites(session, user_id=db_user.id, limit=PAGE_SIZE, offset=offset)
    if not page.items:
        await callback.message.edit_text("В избранном пока нет сохранённых рецептов ⭐")
        await callback.answer()
        return
    kb = _build_page_kb(page, prefix="fav", extra_arg="list", offset=offset)
    await callback.message.edit_text(
        "⭐ <b>Ваши избранные рецепты:</b>", parse_mode="HTML", reply_markup=kb
    )
    await callback.answer()


async def _show_recipe_view(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: UserInfo,
    recipe_id: int,
    rest: str,
) -> None:
    details = await get_recipe_details(session, recipe_id=recipe_id)
    if details is None:
        await callback.answer("Рецепт не найден", show_alert=True)
        return

    fav = await is_favorite(session, user_id=db_user.id, recipe_id=recipe_id)
    fav_text = "💔 Убрать из избранного" if fav else "⭐ В избранное"
    fav_cb = f"toggle_fav:{recipe_id}:{rest}"

    parts = rest.split(":", 2)
    src_prefix = parts[0] if len(parts) > 0 else "cat"
    src_arg = parts[1] if len(parts) > 1 else "show"
    src_offset = parts[2] if len(parts) > 2 else "0"
    back_cb = f"{src_prefix}:{src_arg}:{src_offset}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=fav_text, callback_data=fav_cb)],
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data=back_cb)],
        ]
    )
    if callback.message:
        await callback.message.edit_text(
            _format_recipe_card(details), parse_mode="HTML", reply_markup=kb
        )


@router.callback_query(F.data.startswith("recipe:"))
async def cb_recipe_details(
    callback: CallbackQuery, session: AsyncSession, db_user: UserInfo
) -> None:
    if not callback.data:
        return
    _, recipe_id_str, rest = callback.data.split(":", 2)
    await _show_recipe_view(callback, session, db_user, int(recipe_id_str), rest)
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_fav:"))
async def cb_toggle_favorite(
    callback: CallbackQuery, session: AsyncSession, db_user: UserInfo
) -> None:
    if not callback.data:
        return
    _, recipe_id_str, rest = callback.data.split(":", 2)
    recipe_id = int(recipe_id_str)

    fav = await is_favorite(session, user_id=db_user.id, recipe_id=recipe_id)
    if fav:
        await remove_favorite(session, user_id=db_user.id, recipe_id=recipe_id)
        await callback.answer("Удалено из избранного")
    else:
        await add_favorite(session, user_id=db_user.id, recipe_id=recipe_id)
        await callback.answer("Добавлено в избранное ⭐")

    await _show_recipe_view(callback, session, db_user, recipe_id, rest)


@router.message(F.text == "Поиск")
async def start_search(message: Message, state: FSMContext) -> None:
    await message.answer("Введите название блюда для поиска 🔍", reply_markup=cancel_kb)
    await state.set_state(SearchState.waiting_for_query)


@router.message(SearchState.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext, session: AsyncSession) -> None:
    query = (message.text or "").strip()
    await state.clear()
    await message.answer(
        f"Ищу рецепты по запросу: <b>{query}</b>...",
        parse_mode="HTML",
        reply_markup=main_kb,
    )

    page = await search_by_title(session, query=query, limit=PAGE_SIZE, offset=0)
    if not page.items:
        await message.answer("По вашему запросу ничего не найдено 😔")
        return

    safe_query = query.replace(":", "")[:20]
    kb = _build_page_kb(page, prefix="srch", extra_arg=safe_query, offset=0)
    await message.answer(
        f"Результаты поиска по запросу «<b>{query}</b>»:",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("srch:"))
async def cb_search_page(callback: CallbackQuery, session: AsyncSession) -> None:
    if not callback.data or not callback.message:
        return
    _, query, offset_str = callback.data.split(":", 2)
    offset = int(offset_str)
    page = await search_by_title(session, query=query, limit=PAGE_SIZE, offset=offset)
    kb = _build_page_kb(page, prefix="srch", extra_arg=query, offset=offset)
    await callback.message.edit_text(
        f"Результаты поиска по запросу «<b>{query}</b>»:",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(F.text == "Предложить рецепт")
async def add_recipe_start(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Отлично! Давайте добавим ваш рецепт.\n\nВведите <b>название блюда</b>:",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AddRecipeState.waiting_for_name)


@router.message(AddRecipeState.waiting_for_name)
async def add_recipe_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.update_data(recipe_name=message.text)
    cats = await list_categories(session)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=c) for c in cats], [KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True,
    )
    await message.answer("Выберите <b>категорию блюда</b>:", parse_mode="HTML", reply_markup=kb)
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
        "Напишите <b>краткое описание блюда</b>:",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(AddRecipeState.waiting_for_desc)


@router.message(AddRecipeState.waiting_for_desc)
async def add_recipe_desc(message: Message, state: FSMContext) -> None:
    await state.update_data(recipe_desc=message.text)
    await message.answer(
        "Введите <b>калорийность на 100 г</b> (числом, например <code>250</code>):",
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
        await message.answer("Введите положительное число (например, 250):")
        return
    await state.update_data(recipe_kcal=str(kcal))
    await message.answer(
        "Введите <b>ингредиенты</b> (каждый с новой строки, например: <i>Молоко - 200 мл</i>):",
        parse_mode="HTML",
    )
    await state.set_state(AddRecipeState.waiting_for_ingredients)


@router.message(AddRecipeState.waiting_for_ingredients)
async def add_recipe_ingredients(message: Message, state: FSMContext) -> None:
    await state.update_data(recipe_ings=message.text or "")
    await message.answer(
        "Введите <b>шаги приготовления</b> (каждый шаг с новой строки):",
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
    raw_ings = user_data.get("recipe_ings") or ""
    raw_steps = message.text or ""

    ings = [
        IngredientAmount(name=line.strip(), quantity=None, unit=None)
        for line in raw_ings.split("\n")
        if line.strip()
    ]
    steps = [line.strip() for line in raw_steps.split("\n") if line.strip()]

    new_recipe = NewRecipe(
        title=recipe_name,
        description=recipe_desc,
        calories_kcal=recipe_kcal,
        categories=[recipe_cat],
        ingredients=ings,
        steps=steps,
    )
    await create_proposed_recipe(session, author_id=db_user.id, data=new_recipe)

    text = (
        f"✅ <b>Рецепт отправлен на модерацию!</b>\n\n"
        f"<b>Название:</b> {recipe_name}\n"
        f"<b>Категория:</b> {recipe_cat}\n"
        f"<b>Калорийность:</b> {_fmt_dec(recipe_kcal)} ккал\n"
        f"<b>Ингредиентов:</b> {len(ings)} | <b>Шагов:</b> {len(steps)}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_kb)
    await state.clear()


@router.message(Command("moderate"))
async def cmd_moderate(message: Message, session: AsyncSession) -> None:
    page = await list_pending_recipes(session, limit=PAGE_SIZE, offset=0)
    if not page.items:
        await message.answer("📋 Очередь модерации пуста.")
        return
    rows = [
        [InlineKeyboardButton(text=f"⏳ {item.title}", callback_data=f"mod_view:{item.id}")]
        for item in page.items
    ]
    await message.answer(
        "📋 <b>Рецепты на модерации:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("mod_act:"))
async def cb_moderate_action(
    callback: CallbackQuery, session: AsyncSession, db_user: UserInfo
) -> None:
    if not callback.data or not callback.message:
        return
    _, recipe_id_str, decision = callback.data.split(":", 2)
    approved = decision == "approve"
    await moderate_recipe(
        session,
        recipe_id=int(recipe_id_str),
        moderator_id=db_user.id,
        approved=approved,
    )
    status_text = "✅ Рецепт одобрен!" if approved else "❌ Рецепт отклонён."
    await callback.message.edit_text(status_text)
    await callback.answer()
