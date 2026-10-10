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

from sqlalchemy.ext.asyncio import AsyncSession

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


# ---------------------------------------------------------------------------
# Стаб-данные (удалятся вместе с TODO)
# ---------------------------------------------------------------------------


def _preview(recipe_id: int, title: str, minutes: int, kcal: str) -> RecipePreview:
    return RecipePreview(
        id=recipe_id,
        title=title,
        cook_time_minutes=minutes,
        calories_kcal=Decimal(kcal),
        rating_avg=None,
        reviews_count=0,
    )


_STUB_PREVIEWS = [
    _preview(1, "Овсянка с бананом и корицей", 10, "320.50"),
    _preview(2, "Омлет со шпинатом", 15, "250.00"),
    _preview(3, "Творожная запеканка", 40, "380.00"),
]

_STUB_DETAILS = RecipeDetails(
    id=1,
    title="Овсянка с бананом и корицей",
    description="Быстрый сытный завтрак",
    cook_time_minutes=10,
    servings=1,
    calories_kcal=Decimal("320.50"),
    proteins_g=Decimal("10.20"),
    fats_g=Decimal("7.10"),
    carbs_g=Decimal("55.40"),
    image_file_id=None,
    categories=["Завтрак"],
    diets=["Вегетарианское"],
    ingredients=[
        IngredientAmount("Овсяные хлопья", Decimal("50"), "г"),
        IngredientAmount("Молоко", Decimal("200"), "мл"),
        IngredientAmount("Банан", Decimal("1"), "шт"),
    ],
    steps=[
        "Хлопья залить молоком, довести до кипения",
        "Варить 5 минут, помешивая",
        "Добавить нарезанный банан и корицу",
    ],
    rating_avg=None,
    reviews_count=0,
)


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
    # TODO(спринт 2): SELECT по telegram_id + INSERT/UPDATE
    return UserInfo(
        id=1,
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        is_admin=False,
    )


# ---------------------------------------------------------------------------
# Справочники (кнопки каталога)
# ---------------------------------------------------------------------------


async def list_categories(session: AsyncSession) -> list[str]:
    """Категории для кнопок каталога (US1). Источник - справочник в БД."""
    # TODO(спринт 2): SELECT name FROM categories
    return ["Завтрак", "Обед", "Ужин"]


async def list_diets(session: AsyncSession) -> list[str]:
    """Диеты для кнопок каталога (US1)."""
    # TODO(спринт 2): SELECT name FROM diets
    return ["Вегетарианское", "Веганское", "Без глютена", "Кето"]


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
    """
    # TODO(спринт 2, карточка 1.4): JOIN по recipe_categories/recipe_diets,
    # диапазон калорий, status='approved', пагинация через limit + 1
    return RecipePage(items=list(_STUB_PREVIEWS), has_more=False)


async def search_by_title(
    session: AsyncSession,
    query: str,
    limit: int = 5,
    offset: int = 0,
) -> RecipePage:
    """Поиск подстроки в названии без учёта регистра (US2, карточка 2.2).

    Только approved. Кириллица сравнивается через func.lower() с обеих
    сторон: LIKE в SQLite регистронезависим только для латиницы.
    """
    # TODO(спринт 2, карточка 2.2)
    return RecipePage(items=list(_STUB_PREVIEWS), has_more=False)


# ---------------------------------------------------------------------------
# Карточка (US3, US4)
# ---------------------------------------------------------------------------


async def get_recipe_details(session: AsyncSession, recipe_id: int) -> RecipeDetails | None:
    """Полная карточка (US3 + US4, карточка 3.1). None - рецепта нет.

    Статус НЕ фильтруется: approved гарантируют списки (find/search),
    а модерации и подтверждению предложения нужен доступ к pending.
    """
    # TODO(спринт 2, карточка 3.1): один запрос с selectinload шагов,
    # ингредиентов, категорий и диет
    return _STUB_DETAILS if recipe_id == 1 else None


# ---------------------------------------------------------------------------
# Избранное (US5)
# ---------------------------------------------------------------------------


async def add_favorite(session: AsyncSession, user_id: int, recipe_id: int) -> None:
    """Добавить в избранное (US5, карточка 5.2). Идемпотентно: повтор - не ошибка."""
    # TODO(спринт 2, карточка 5.2): INSERT с игнорированием дубля


async def remove_favorite(session: AsyncSession, user_id: int, recipe_id: int) -> bool:
    """Убрать из избранного (US5, карточка 5.2).

    Returns:
        True - убрали, False - его и не было (кнопка не должна падать).
    """
    # TODO(спринт 2, карточка 5.2)
    return True


async def list_favorites(
    session: AsyncSession,
    user_id: int,
    limit: int = 5,
    offset: int = 0,
) -> RecipePage:
    """Избранное пользователя (US5), новые сверху."""
    # TODO(спринт 2, карточка 5.2)
    return RecipePage(items=[], has_more=False)


async def is_favorite(session: AsyncSession, user_id: int, recipe_id: int) -> bool:
    """Проверка для кнопки ☆ на карточке (карточка 5.1)."""
    # TODO(спринт 2, карточка 5.2)
    return False


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
