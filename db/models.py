from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    """Пользователь бота (US5–US8)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Telegram ID - не Primay Key: искусственный id стабильнее внешнего идентификатора
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    recipes: Mapped[list["Recipe"]] = relationship(
        back_populates="author", foreign_keys="Recipe.author_id"
    )
    favorite_recipes: Mapped[list["Recipe"]] = relationship(
        secondary="favorites", back_populates="favorited_by"
    )


class Recipe(Base):
    """Рецепт. Пользовательские рецепты попадают сюда же со статусом pending (US7)."""

    __tablename__ = "recipes"
    __table_args__ = (
        CheckConstraint("cook_time_minutes > 0", name="ck_recipes_cook_time"),
        CheckConstraint("servings > 0", name="ck_recipes_servings"),
        CheckConstraint("calories_kcal >= 0", name="ck_recipes_calories"),
        CheckConstraint("proteins_g >= 0", name="ck_recipes_proteins"),
        CheckConstraint("fats_g >= 0", name="ck_recipes_fats"),
        CheckConstraint("carbs_g >= 0", name="ck_recipes_carbs"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_recipes_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Автор nullable: пользователь удалён - рецепты остались (ON DELETE SET NULL)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(120), index=True)  # US2: поиск
    description: Mapped[str | None] = mapped_column(Text)
    cook_time_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    servings: Mapped[int | None] = mapped_column(SmallInteger)
    # Калорийность nullable, но обязательна до публикации - проверяет модерация (US8)
    calories_kcal: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), index=True)
    proteins_g: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # US4
    fats_g: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # US4
    carbs_g: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # US4
    image_file_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    moderated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # onupdate - уровень ORM; raw SQL обновления идут только через репозиторий
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # два FK на users (author, moderator) -> обязательно указывать foreign_keys
    author: Mapped["User | None"] = relationship(back_populates="recipes", foreign_keys=[author_id])
    moderator: Mapped["User | None"] = relationship(foreign_keys=[moderated_by])
    steps: Mapped[list["RecipeStep"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeStep.step_number",
    )
    categories: Mapped[list["Category"]] = relationship(
        secondary="recipe_categories", back_populates="recipes"
    )
    diets: Mapped[list["Diet"]] = relationship(secondary="recipe_diets", back_populates="recipes")
    ingredient_links: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["Review"]] = relationship(back_populates="recipe")
    favorited_by: Mapped[list["User"]] = relationship(
        secondary="favorites", back_populates="favorite_recipes"
    )


class RecipeStep(Base):
    """Шаг приготовления (US3). Составной PK (recipe_id, step_number)."""

    __tablename__ = "recipe_steps"

    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True
    )
    step_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    instruction: Mapped[str] = mapped_column(Text)

    recipe: Mapped["Recipe"] = relationship(back_populates="steps")


class Category(Base):
    """Категория приёма пищи: завтрак / обед / ужин (US1)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    recipes: Mapped[list["Recipe"]] = relationship(
        secondary="recipe_categories", back_populates="categories"
    )


class Diet(Base):
    """Диетическое предпочтение (US1)."""

    __tablename__ = "diets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    recipes: Mapped[list["Recipe"]] = relationship(secondary="recipe_diets", back_populates="diets")


class Ingredient(Base):
    """Ингредиент, общий справочник."""

    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class RecipeIngredient(Base):
    """Связка рецепт-ингредиент с количеством (M2M с данными)."""

    __tablename__ = "recipe_ingredients"
    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_recipe_ingredients_quantity"),)

    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True
    )
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="RESTRICT"), primary_key=True
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    unit: Mapped[str | None] = mapped_column(String(20))  # г / мл / шт

    recipe: Mapped["Recipe"] = relationship(back_populates="ingredient_links")
    ingredient: Mapped["Ingredient"] = relationship()


class Review(Base):
    """Отзыв (US6). Один на пользователя: UNIQUE(recipe_id, user_id)."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("recipe_id", "user_id", name="uq_reviews_recipe_user"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)  # искусственный PK
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(SmallInteger)
    text: Mapped[str | None] = mapped_column(Text)  # оценка без текста - валидно
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    recipe: Mapped["Recipe"] = relationship(back_populates="reviews")
    user: Mapped["User"] = relationship()


# M2M без доп. данных - обычные таблицы, ORM-классы не нужны.
# Отдельный idx_favorites_user не требуется: user_id - первый столбец составного PK
recipe_categories = Table(
    "recipe_categories",
    Base.metadata,
    Column("recipe_id", ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="RESTRICT"), primary_key=True),
)

recipe_diets = Table(
    "recipe_diets",
    Base.metadata,
    Column("recipe_id", ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True),
    Column("diet_id", ForeignKey("diets.id", ondelete="RESTRICT"), primary_key=True),
)

favorites = Table(
    "favorites",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("recipe_id", ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime, server_default=func.now()),
)
