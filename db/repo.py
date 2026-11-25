"""Репозиторий: DTO + функции доступа к данным (контракт спринта 1).

Соглашения контракта:
- Все функции асинхронные, первым параметром принимают готовую AsyncSession
  (создаёт middleware бота или тесты - db.engine.make_session_factory).
- user_id везде - внутренний id (users.id), НЕ telegram_id. Соответствие
  разрешается один раз через get_or_create_user (мидлварь на апдейт).
- Функции, которые пишут, сами делают commit.
- Пустой результат - пустой RecipePage/список, а не исключение.
- Калории, БЖУ и количества - Decimal; форматирование ("320,50 ккал")
  остаётся на стороне хендлеров.
- rating_avg/reviews_count агрегируются из отзывов (US6, спринт 3):
  без отзывов это None/0 - рендер должен это переживать.
- Сигнатуру поменять можно только обсуждением, а не молчаливой правкой.

Спринт 2: справочники, каталог, поиск, карточка, избранное и
get_or_create_user. Спринт 3: отзывы (US6), предложения (US7) и
модерация (US8). Стабов больше нет - всё ходит в базу.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    Category,
    Diet,
    Ingredient,
    Recipe,
    RecipeIngredient,
    RecipeStep,
    Review,
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
    rating_avg: float | None  # агрегат отзывов (US6); None - отзывов нет
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
    rating_avg: float | None  # агрегат отзывов (US6); None - отзывов нет
    reviews_count: int


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


def _to_preview(recipe: Recipe, rating_avg, reviews_count: int) -> RecipePreview:
    """(Recipe, avg, count) -> строка списка.

    AVG в SQLite возвращает float, в PostgreSQL - numeric; float() в Python
    приводит оба диалекта к одному типу DTO.
    """
    return RecipePreview(
        id=recipe.id,
        title=recipe.title,
        cook_time_minutes=recipe.cook_time_minutes,
        calories_kcal=recipe.calories_kcal,
        rating_avg=float(rating_avg) if rating_avg is not None else None,
        reviews_count=int(reviews_count),
    )


def _rating_subqueries():
    """Скалярные подзапросы: средняя оценка и число отзывов рецепта.

    Коррелируются с Recipe в любом списке (каталог, поиск, избранное,
    очередь модерации) - JOIN основного запроса им не мешает.
    """
    rating_avg = (
        select(func.avg(Review.rating))
        .where(Review.recipe_id == Recipe.id)
        .correlate(Recipe)
        .scalar_subquery()
    )
    reviews_count = (
        select(func.count())
        .select_from(Review)
        .where(Review.recipe_id == Recipe.id)
        .correlate(Recipe)
        .scalar_subquery()
    )
    return rating_avg, reviews_count


async def _recipe_page(
    session: AsyncSession,
    stmt: Select,
    limit: int,
    offset: int,
) -> RecipePage:
    """Превратить select(Recipe) в страницу с рейтингами.

    Тянем limit + 1 строку: лишняя строка означает has_more=True.
    Порядок в stmt обязан быть детерминированным (order_by id),
    иначе offset выдаёт дубли и пропуски на соседних страницах.
    """
    rating_avg, reviews_count = _rating_subqueries()
    stmt = stmt.add_columns(rating_avg, reviews_count)
    rows = (await session.execute(stmt.limit(limit + 1).offset(offset))).all()
    has_more = len(rows) > limit
    items = [_to_preview(recipe, avg, cnt) for recipe, avg, cnt in rows[:limit]]
    return RecipePage(items=items, has_more=has_more)


def _author_name(user: User) -> str:
    """Имя автора отзыва: first_name -> username -> заглушка id."""
    return user.first_name or user.username or f"user{user.id}"


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
    rating_avg, reviews_count = _rating_subqueries()
    stmt = (
        select(Recipe, rating_avg, reviews_count)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.steps),
            selectinload(Recipe.categories),
            selectinload(Recipe.diets),
            selectinload(Recipe.ingredient_links).selectinload(RecipeIngredient.ingredient),
        )
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    recipe, avg, cnt = row
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
        rating_avg=float(avg) if avg is not None else None,
        reviews_count=int(cnt),
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
    Заменяет отзыв ЦЕЛИКОМ: text=None в повторном вызове очищает текст
    ("оценка без отзыва" - валидный кейс). Бот одной формой присылает и
    оценку, и текст - частичного обновления нет.
    rating 1..5 - дублирует CHECK уровня БД, но ValueError до записи
    дружелюбнее IntegrityError после. Несуществующий recipe_id упадёт
    на FK-ограничении: хендлер получает id из списков, такого не бывает.
    """
    if not 1 <= rating <= 5:
        raise ValueError(f"rating должен быть целым 1..5, получили {rating}")
    review = await session.scalar(
        select(Review).where(Review.recipe_id == recipe_id, Review.user_id == user_id)
    )
    if review is None:
        review = Review(recipe_id=recipe_id, user_id=user_id, rating=rating, text=text)
        session.add(review)
    else:
        review.rating = rating
        review.text = text
    await session.commit()


async def get_recipe_reviews(
    session: AsyncSession,
    recipe_id: int,
    limit: int = 10,
    offset: int = 0,
) -> list[ReviewEntry]:
    """Отзывы рецепта, новые сверху (US6). Рендер - Ильхам (карточка 6.3).

    Возвращает список без has_more: бот показывает кнопку "Дальше",
    если пришло ровно limit штук. created_at в SQLite точен до секунды,
    поэтому тай-брейк по id отзыва.
    """
    stmt = (
        select(Review, User)
        .join(Review.user)
        .where(Review.recipe_id == recipe_id)
        .order_by(Review.created_at.desc(), Review.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ReviewEntry(
            author_name=_author_name(user),
            rating=review.rating,
            text=review.text,
            created_at=review.created_at,
        )
        for review, user in rows
    ]


async def get_user_review(
    session: AsyncSession,
    user_id: int,
    recipe_id: int,
) -> ReviewEntry | None:
    """Отзыв текущего пользователя: "ваша оценка" и редактирование (карточка 6.4)."""
    row = (
        await session.execute(
            select(Review, User)
            .join(Review.user)
            .where(Review.recipe_id == recipe_id, Review.user_id == user_id)
        )
    ).first()
    if row is None:
        return None
    review, user = row
    return ReviewEntry(
        author_name=_author_name(user),
        rating=review.rating,
        text=review.text,
        created_at=review.created_at,
    )


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
    ингредиенты (get-or-create по названию), M2M-связки.
    ValueError, если категория/диета не из справочника или ингредиент
    указан дважды (иначе повтор упал бы на составном PK).

    Порядок важен: все SELECT - до session.add(recipe). Любой await между
    add и присваиванием коллекций делает autoflush, объект становится
    persistent, и замена коллекции тянет ленивую загрузку (запрещена
    в async). Паттерн перенесён из db/seed.py.
    """
    # 1. Валидация справочников: неизвестные имена - все разом, не по одному
    known_categories: dict[str, Category] = {}
    if data.categories:
        rows = await session.scalars(select(Category).where(Category.name.in_(data.categories)))
        known_categories = {c.name: c for c in rows}
        unknown = [name for name in data.categories if name not in known_categories]
        if unknown:
            raise ValueError(f"неизвестные категории: {', '.join(unknown)}")

    known_diets: dict[str, Diet] = {}
    if data.diets:
        rows = await session.scalars(select(Diet).where(Diet.name.in_(data.diets)))
        known_diets = {d.name: d for d in rows}
        unknown = [name for name in data.diets if name not in known_diets]
        if unknown:
            raise ValueError(f"неизвестные диеты: {', '.join(unknown)}")

    # 2. Дубли ингредиентов в форме: составной PK (recipe_id, ingredient_id)
    #    повтор упал бы на INSERT - честный ValueError лучше
    seen_ingredients: set[str] = set()
    for item in data.ingredients:
        name = item.name.strip()
        if name in seen_ingredients:
            raise ValueError(f"ингредиент «{name}» указан дважды")
        seen_ingredients.add(name)

    # 3. Существующие ингредиенты - одним запросом (get-or-create ниже)
    ingredients: dict[str, Ingredient] = {}
    if seen_ingredients:
        rows = await session.scalars(
            select(Ingredient).where(Ingredient.name.in_(seen_ingredients))
        )
        ingredients = {i.name: i for i in rows}

    # 4. Рецепт: add -> присваивания БЕЗ await между ними -> один commit
    recipe = Recipe(
        title=data.title.strip(),
        description=data.description,
        cook_time_minutes=data.cook_time_minutes,
        servings=data.servings,
        calories_kcal=data.calories_kcal,
        proteins_g=data.proteins_g,
        fats_g=data.fats_g,
        carbs_g=data.carbs_g,
        image_file_id=data.image_file_id,
        status="pending",  # решение за модератором (US8)
        author_id=author_id,
    )
    session.add(recipe)

    # дубли категорий/диет в форме - это чекбоксы, молча схлопываем
    recipe.categories = [known_categories[name] for name in dict.fromkeys(data.categories)]
    recipe.diets = [known_diets[name] for name in dict.fromkeys(data.diets)]
    recipe.steps = [
        RecipeStep(step_number=number, instruction=step)
        for number, step in enumerate(data.steps, start=1)
    ]
    for item in data.ingredients:
        name = item.name.strip()
        ingredient = ingredients.get(name)
        if ingredient is None:
            ingredient = Ingredient(name=name)
            session.add(ingredient)
            ingredients[name] = ingredient
        session.add(
            RecipeIngredient(
                recipe=recipe,
                ingredient=ingredient,
                quantity=item.quantity,
                unit=item.unit,
            )
        )
    await session.commit()
    return recipe.id


async def list_pending_recipes(
    session: AsyncSession,
    limit: int = 5,
    offset: int = 0,
) -> RecipePage:
    """Очередь модерации (US8, карточка 8.2): pending, старые сверху.

    Старые сверху - модератор разбирает залежавшееся раньше нового.
    created_at в SQLite точен до секунды - тай-брейк по id.
    """
    stmt = select(Recipe).where(Recipe.status == "pending").order_by(Recipe.created_at, Recipe.id)
    return await _recipe_page(session, stmt, limit, offset)


async def moderate_recipe(
    session: AsyncSession,
    recipe_id: int,
    moderator_id: int,
    approved: bool,
) -> None:
    """Одобрить или отклонить (US8, карточка 8.2): status + moderated_by/at.

    При approved=True калорийность обязана быть заполнена, иначе ValueError:
    "калорийность nullable, но обязательна до публикации" (§3 ворклога).
    Статус не меняется - черновик остаётся в очереди, модератор
    дозаполняет КБЖУ и повторяет решение. Повторная модерация уже
    разобранного рецепта разрешена (модератор передумал).
    """
    recipe = await session.get(Recipe, recipe_id)
    if recipe is None:
        raise ValueError(f"рецепт #{recipe_id} не найден")
    if approved and recipe.calories_kcal is None:
        raise ValueError("калорийность обязательна до публикации (US8)")

    recipe.status = "approved" if approved else "rejected"
    recipe.moderated_by = moderator_id
    # naive UTC в одну шкалу с server_default func.now() в SQLite
    recipe.moderated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
