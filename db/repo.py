"""Контракт репозитория: DTO + сигнатуры + стабы.

Соглашения контракта:
- Все функции асинхронные, первым параметром принимают готовую AsyncSession
  (создаёт middleware бота или тесты - db.engine.make_session_factory).
- user_id везде - внутренний id (users.id), НЕ telegram_id. Соответствие
  разрешается один раз через get_or_create_user (мидлварь на апдейт).
- Функции, которые пишут, сами делают commit.
- Пустой результат - пустой RecipePage/список, а не исключение.
- Калории, БЖУ и количества - Decimal; форматирование ("320,50 ккал")
  остаётся на стороне хендлеров.
- rating_avg/reviews_count по-настоящему заполнятся в спринте 3 (US6);
  до тех пор это None/0 - рендер должен это переживать.
- Сигнатуру поменять можно только обсуждением, а не молчаливой правкой.

Тела - стабы с правдоподобными данными: на них Ильхам пишет и смотрит
хендлеры до появления реальных запросов (карточки 1.4, 2.2, 3.1, 5.2).
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    Category,
    Diet,
    Recipe,
    RecipeIngredient,
    User,
    favorites,
)

# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class RecipePreview:
    """Строка списка (каталог, поиск, избранное, очередь модерации)."""

    id: int
    title: str
    cook_time_minutes: int | None
    calories_kcal: Decimal | None
    rating_avg: float | None  # со спринта 3 (US6)
    reviews_count: int


@dataclass(slots=True, frozen=True)
class RecipePage:
    """Страница списка. has_more=True - есть следующая (кнопка "Дальше")."""

    items: list[RecipePreview]
    has_more: bool


@dataclass(slots=True, frozen=True)
class RecipeFilters:
    """Фильтры каталога (US1). None = фильтр не применяется."""

    category: str | None = None  # "Завтрак" / "Обед" / "Ужин"
    diet: str | None = None
    min_calories: Decimal | None = None
    max_calories: Decimal | None = None


@dataclass(slots=True, frozen=True)
class IngredientAmount:
    name: str
    quantity: Decimal | None
    unit: str | None  # г / мл / шт


@dataclass(slots=True, frozen=True)
class RecipeDetails:
    """Полная карточка рецепта (US3 + US4)."""

    id: int
    title: str
    description: str | None
    cook_time_minutes: int | None
    servings: int | None
    calories_kcal: Decimal | None
    proteins_g: Decimal | None
    fats_g: Decimal | None
    carbs_g: Decimal | None
    image_file_id: str | None
    categories: list[str]
    diets: list[str]
    ingredients: list[IngredientAmount]
    steps: list[str]  # инструкции, отсортированы по step_number
    rating_avg: float | None  # со спринта 3
    reviews_count: int  # со спринта 3


@dataclass(slots=True, frozen=True)
class UserInfo:
    """Пользователь после разрешения telegram_id -> users.id."""

    id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    is_admin: bool


@dataclass(slots=True, frozen=True)
class ReviewEntry:
    author_name: str  # имя для показа, склеивает repo
    rating: int
    text: str | None
    created_at: datetime


@dataclass(slots=True)
class NewRecipe:
    """Данные формы "Предложить рецепт" (US7). Статус pending ставит repo."""

    title: str
    description: str | None = None
    cook_time_minutes: int | None = None
    servings: int | None = None
    calories_kcal: Decimal | None = None
    proteins_g: Decimal | None = None
    fats_g: Decimal | None = None
    carbs_g: Decimal | None = None
    image_file_id: str | None = None
    categories: list[str] = field(default_factory=list)
    diets: list[str] = field(default_factory=list)
    ingredients: list[IngredientAmount] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class CategoryDTO:
    id: int
    name: str


# ---------------------------------------------------------------------------
# Внутренние хелперы (не для хендлеров)
# ---------------------------------------------------------------------------


def _to_preview(recipe: Recipe) -> RecipePreview:
    """Recipe -> строка списка. Оценки появятся в спринте 3 (US6)."""
    return RecipePreview(
        id=recipe.id,
        title=recipe.title,
        cook_time_minutes=recipe.cook_time_minutes,
        calories_kcal=recipe.calories_kcal,
        rating_avg=None,
        reviews_count=0,
    )


async def _recipe_page(
    session: AsyncSession,
    stmt: Select,
    limit: int,
    offset: int,
) -> RecipePage:
    """Превратить select(Recipe) в страницу.

    Тянем limit + 1 строку: лишняя строка означает has_more=True.
    Порядок в stmt обязан быть детерминированным (order_by id),
    иначе offset выдаёт дубли и пропуски на соседних страницах.
    """
    rows = (await session.scalars(stmt.limit(limit + 1).offset(offset))).all()
    has_more = len(rows) > limit
    return RecipePage(items=[_to_preview(r) for r in rows[:limit]], has_more=has_more)


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> UserInfo:
    """Пользователь по telegram_id; создаёт при первом обращении.

    Заодно обновляет username/имена - они в Telegram меняются.
    Точка разрешения telegram_id -> users.id: дальше все функции
    работают только с внутренним id. Даёт is_admin для модерации (US8).
    """
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        session.add(user)
        await session.commit()
    elif (user.username, user.first_name, user.last_name) != (
        username,
        first_name,
        last_name,
    ):
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        await session.commit()
    return UserInfo(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        is_admin=user.is_admin,
    )


# ---------------------------------------------------------------------------
# Справочники (кнопки каталога)
# ---------------------------------------------------------------------------


async def list_categories(session: AsyncSession) -> list[str]:
    """Имена категорий для Reply-кнопок каталога (US1). Источник - справочник в БД."""
    rows = await session.scalars(select(Category).order_by(Category.id))
    return [c.name for c in rows]


async def list_diets(session: AsyncSession) -> list[str]:
    """Имена диет для кнопок каталога (US1)."""
    rows = await session.scalars(select(Diet).order_by(Diet.id))
    return [d.name for d in rows]


async def get_categories(session: AsyncSession) -> list[CategoryDTO]:
    """id + имя категорий: для inline-кнопок, где callback_data несёт id, а не текст."""
    rows = await session.scalars(select(Category).order_by(Category.id))
    return [CategoryDTO(id=c.id, name=c.name) for c in rows]


async def get_diets(session: AsyncSession) -> list[CategoryDTO]:
    """id + имя диет: см. get_categories."""
    rows = await session.scalars(select(Diet).order_by(Diet.id))
    return [CategoryDTO(id=d.id, name=d.name) for d in rows]


# ---------------------------------------------------------------------------
# Каталог и поиск (US1, US2)
# ---------------------------------------------------------------------------


async def find_recipes(
    session: AsyncSession,
    filters: RecipeFilters | None = None,
    limit: int = 5,
    offset: int = 0,
) -> RecipePage:
    """Каталог с фильтрами (US1, карточка 1.4).

    Только approved. Активные фильтры объединяются через AND.
    "Дальше" = тот же вызов с offset += limit.
    Фильтры берутся по именам из справочника (как в RecipeFilters);
    рецепты без калорийности в диапазон не попадают (NULL не сравнивается).
    """
    stmt = select(Recipe).where(Recipe.status == "approved")
    if filters is not None:
        if filters.category is not None:
            stmt = stmt.join(Recipe.categories).where(Category.name == filters.category)
        if filters.diet is not None:
            stmt = stmt.join(Recipe.diets).where(Diet.name == filters.diet)
        if filters.min_calories is not None:
            stmt = stmt.where(Recipe.calories_kcal >= filters.min_calories)
        if filters.max_calories is not None:
            stmt = stmt.where(Recipe.calories_kcal <= filters.max_calories)
    stmt = stmt.order_by(Recipe.id)
    return await _recipe_page(session, stmt, limit, offset)


async def search_by_title(
    session: AsyncSession,
    query: str,
    limit: int = 5,
    offset: int = 0,
) -> RecipePage:
    """Поиск подстроки в названии без учёта регистра (US2, карточка 2.2).

    Только approved. Кириллица сравнивается через func.lower() с обеих
    сторон: LIKE в SQLite регистронезависим только для латиницы.
    autoescape гасит % и _ в запросе пользователя; пустой запрос
    после strip() матчит все approved (боту в пустой запрос лучше не звать).
    """
    q = query.strip().lower()
    stmt = (
        select(Recipe)
        .where(Recipe.status == "approved")
        .where(func.lower(Recipe.title).contains(q, autoescape=True))
        .order_by(Recipe.id)
    )
    return await _recipe_page(session, stmt, limit, offset)


# ---------------------------------------------------------------------------
# Карточка (US3, US4)
# ---------------------------------------------------------------------------


async def get_recipe_details(session: AsyncSession, recipe_id: int) -> RecipeDetails | None:
    """Полная карточка (US3 + US4, карточка 3.1). None - рецепта нет.

    Статус НЕ фильтруется: approved гарантируют списки (find/search),
    а модерации и подтверждению предложения нужен доступ к pending.
    """
    stmt = (
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.steps),
            selectinload(Recipe.categories),
            selectinload(Recipe.diets),
            selectinload(Recipe.ingredient_links).selectinload(RecipeIngredient.ingredient),
        )
    )
    recipe = (await session.scalars(stmt)).first()
    if recipe is None:
        return None
    return RecipeDetails(
        id=recipe.id,
        title=recipe.title,
        description=recipe.description,
        cook_time_minutes=recipe.cook_time_minutes,
        servings=recipe.servings,
        calories_kcal=recipe.calories_kcal,
        proteins_g=recipe.proteins_g,
        fats_g=recipe.fats_g,
        carbs_g=recipe.carbs_g,
        image_file_id=recipe.image_file_id,
        categories=[c.name for c in recipe.categories],
        diets=[d.name for d in recipe.diets],
        ingredients=[
            IngredientAmount(
                name=link.ingredient.name,
                quantity=link.quantity,
                unit=link.unit,
            )
            for link in sorted(recipe.ingredient_links, key=lambda link: link.ingredient_id)
        ],
        steps=[step.instruction for step in sorted(recipe.steps, key=lambda s: s.step_number)],
        rating_avg=None,
        reviews_count=0,
    )


# ---------------------------------------------------------------------------
# Избранное (US5)
# ---------------------------------------------------------------------------


async def add_favorite(session: AsyncSession, user_id: int, recipe_id: int) -> None:
    """Добавить в избранное (US5, карточка 5.2). Идемпотентно: повтор - не ошибка."""
    existing = await session.scalar(
        select(favorites.c.user_id).where(
            favorites.c.user_id == user_id,
            favorites.c.recipe_id == recipe_id,
        )
    )
    if existing is None:
        await session.execute(insert(favorites).values(user_id=user_id, recipe_id=recipe_id))
        await session.commit()


async def remove_favorite(session: AsyncSession, user_id: int, recipe_id: int) -> bool:
    """Убрать из избранного (US5, карточка 5.2).

    Returns:
        True - убрали, False - его и не было (кнопка не должна падать).
    """
    result = await session.execute(
        delete(favorites).where(
            favorites.c.user_id == user_id,
            favorites.c.recipe_id == recipe_id,
        )
    )
    await session.commit()
    return bool(result.rowcount)


async def list_favorites(
    session: AsyncSession,
    user_id: int,
    limit: int = 5,
    offset: int = 0,
) -> RecipePage:
    """Избранное пользователя (US5), новые сверху.

    favorites - Core-таблица без ORM-класса (M2M без данных, кроме created_at),
    поэтому запросы к ней идут через insert/delete/select от самой таблицы.
    """
    stmt = (
        select(Recipe)
        .join(favorites, favorites.c.recipe_id == Recipe.id)
        .where(favorites.c.user_id == user_id)
        # created_at в SQLite точен до секунды - тай-брейк по recipe_id
        .order_by(favorites.c.created_at.desc(), Recipe.id.desc())
    )
    return await _recipe_page(session, stmt, limit, offset)


async def is_favorite(session: AsyncSession, user_id: int, recipe_id: int) -> bool:
    """Проверка для кнопки ☆ на карточке (карточка 5.1)."""
    existing = await session.scalar(
        select(favorites.c.user_id).where(
            favorites.c.user_id == user_id,
            favorites.c.recipe_id == recipe_id,
        )
    )
    return existing is not None


# ---------------------------------------------------------------------------
# Отзывы (US6)
# ---------------------------------------------------------------------------


async def upsert_review(
    session: AsyncSession,
    user_id: int,
    recipe_id: int,
    rating: int,
    text: str | None = None,
) -> None:
    """Оставить или обновить отзыв (US6, карточка 6.2).

    Повторная оценка = UPDATE, а не вторая строка: UNIQUE (recipe_id, user_id).
    rating 1..5 - дублирует CHECK уровня БД.
    """
    # TODO(спринт 3, карточка 6.2)


async def get_recipe_reviews(
    session: AsyncSession,
    recipe_id: int,
    limit: int = 10,
    offset: int = 0,
) -> list[ReviewEntry]:
    """Отзывы рецепта, новые сверху (US6). Рендер - Ильхам (карточка 6.3)."""
    # TODO(спринт 3, карточка 6.2)
    return []


async def get_user_review(
    session: AsyncSession,
    user_id: int,
    recipe_id: int,
) -> ReviewEntry | None:
    """Отзыв текущего пользователя: "ваша оценка" и редактирование (карточка 6.4)."""
    # TODO(спринт 3, карточка 6.2)
    return None


# ---------------------------------------------------------------------------
# Предложения и модерация (US7, US8)
# ---------------------------------------------------------------------------


async def create_proposed_recipe(
    session: AsyncSession,
    author_id: int,
    data: NewRecipe,
) -> int:
    """Записать предложенный рецепт (US7, карточка 7.2) и вернуть его id.

    Одна транзакция по всем таблицам: recipes (status='pending'), steps,
    ingredients (get-or-create по названию), M2M-связки.
    ValueError, если категория/диета не из справочника.
    """
    # TODO(спринт 3, карточка 7.2)
    return 0


async def list_pending_recipes(
    session: AsyncSession,
    limit: int = 5,
    offset: int = 0,
) -> RecipePage:
    """Очередь модерации (US8, карточка 8.2): pending, старые сверху."""
    # TODO(спринт 3, карточка 8.2)
    return RecipePage(items=[], has_more=False)


async def moderate_recipe(
    session: AsyncSession,
    recipe_id: int,
    moderator_id: int,
    approved: bool,
) -> None:
    """Одобрить или отклонить (US8, карточка 8.2): status + moderated_by/at.

    При approved=True калорийность обязана быть заполнена, иначе ValueError:
    "калорийность nullable, но обязательна до публикации" (§3 ворклога).
    """
    # TODO(спринт 3, карточка 8.2)
