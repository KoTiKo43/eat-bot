"""Сид: демо-рецепты из db/seed_data.json в базу.

Вторая команда демо (первая - alembic upgrade head):

    python -m db.seed

Правила данных (файл заполняет команда, карточка 7б):
- категории и диеты - строго названия из справочника (data-migration);
- калории и БЖУ указываются НА ПОРЦИЮ и обязательны: это витрина (US1, US4);
- cook_time_minutes и servings обязательны и больше нуля;
- steps - непустой список строк (US3);
- description, diets, ingredients - опциональны.

Сид-рецепты - витрина: status='approved', без автора.
Идемпотентность: если в recipes уже есть записи, сид ничего не делает.
Полный перезалив: alembic downgrade base && alembic upgrade head.
"""

import asyncio
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import make_engine, make_session_factory
from db.models import Category, Diet, Ingredient, Recipe, RecipeIngredient, RecipeStep

DATA_PATH = Path(__file__).resolve().parent / "seed_data.json"
DEFAULT_URL = "sqlite+aiosqlite:///./eat.db"

REQUIRED_NUMBERS = (
    "cook_time_minutes",
    "servings",
    "calories_kcal",
    "proteins_g",
    "fats_g",
    "carbs_g",
)
POSITIVE_ONLY = ("cook_time_minutes", "servings")


class SeedDataError(ValueError):
    """Данные не прошли проверку; сообщение содержит весь список проблем."""


def _load(path: Path) -> list[dict]:
    """Читает JSON-массив рецептов с дружелюбными ошибками формата."""
    try:
        # utf-8-sig терпит BOM - файл могут сохранить в Блокноте
        content = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise SeedDataError(f"Не найден {path}. Положите туда демо-рецепты.") from exc
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SeedDataError(f"{path.name}: некорректный JSON - {exc}") from exc
    if not isinstance(data, list):
        raise SeedDataError(f"{path.name}: ожидается JSON-массив рецептов")
    return data


def _dec(data: dict, key: str) -> Decimal:
    """Число из JSON в Decimal, без артефактов float."""
    return Decimal(str(data[key]))


def _validate(data: list[dict], category_names: set[str], diet_names: set[str]) -> None:
    """Проверяет все рецепты и собирает все ошибки разом - удобнее править."""
    if not data:
        raise SeedDataError("в файле сида нет ни одного рецепта")

    errors: list[str] = []
    for idx, recipe in enumerate(data, start=1):
        prefix = f"рецепт #{idx}"
        if not isinstance(recipe, dict):
            errors.append(f"{prefix}: ожидался объект, получено {type(recipe).__name__}")
            continue

        title = recipe.get("title")
        if isinstance(title, str) and title.strip():
            prefix = f"{prefix} «{title.strip()}»"
        else:
            errors.append(f"{prefix}: отсутствует или пустой title")

        for key in REQUIRED_NUMBERS:
            raw = recipe.get(key)
            if raw is None:
                errors.append(f"{prefix}: отсутствует {key}")
                continue
            try:
                value = Decimal(str(raw))
            except InvalidOperation, ValueError:
                errors.append(f"{prefix}: {key}={raw!r} - не число")
                continue
            if key in POSITIVE_ONLY and value <= 0:
                errors.append(f"{prefix}: {key}={raw!r} - должно быть больше нуля")
            elif key not in POSITIVE_ONLY and value < 0:
                errors.append(f"{prefix}: {key}={raw!r} - не может быть отрицательным")

        steps = recipe.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{prefix}: steps - непустой список строк (US3)")
        elif not all(isinstance(step, str) and step.strip() for step in steps):
            errors.append(f"{prefix}: в steps есть пустые или не-строки")

        for key, allowed in (("categories", category_names), ("diets", diet_names)):
            values = recipe.get(key, [])
            if not isinstance(values, list):
                errors.append(f"{prefix}: {key} - список названий")
                continue
            bad = [v for v in values if not isinstance(v, str) or v not in allowed]
            if bad:
                errors.append(
                    f"{prefix}: {key}: {bad} - должны быть названиями из справочника "
                    f"(доступно: {', '.join(sorted(allowed))})"
                )

        ingredients = recipe.get("ingredients", [])
        if not isinstance(ingredients, list):
            errors.append(f"{prefix}: ingredients - список объектов")
            continue
        seen: set[str] = set()
        for num, item in enumerate(ingredients, start=1):
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{prefix}: ингредиент #{num} без названия")
                continue
            if name.strip() in seen:
                errors.append(f"{prefix}: ингредиент «{name.strip()}» указан дважды")
                continue
            seen.add(name.strip())
            quantity = item.get("quantity")
            if quantity is not None:
                try:
                    Decimal(str(quantity))
                except InvalidOperation, ValueError:
                    errors.append(
                        f"{prefix}: ингредиент #{num} «{name.strip()}»: "
                        f"quantity={quantity!r} - не число"
                    )

    if errors:
        raise SeedDataError("Ошибки в данных сида:\n  - " + "\n  - ".join(errors))


async def seed(session: AsyncSession, data: list[dict]) -> int:
    """Заливает рецепты и возвращает количество добавленных.

    0 - если база не пуста (идемпотентность). Валидация идёт до записи,
    поэтому применяется всё или ничего.
    """
    if await session.scalar(select(Recipe.id).limit(1)) is not None:
        print("В recipes уже есть данные - сид пропущен.")
        print("Полный перезалив: alembic downgrade base && alembic upgrade head")
        return 0

    categories = {c.name: c for c in (await session.scalars(select(Category))).all()}
    diets = {d.name: d for d in (await session.scalars(select(Diet))).all()}
    _validate(data, set(categories), set(diets))

    ingredients: dict[str, Ingredient] = {}
    created_ingredients = 0
    loaded: list[str] = []

    for recipe_data in data:
        recipe = Recipe(
            title=recipe_data["title"].strip(),
            description=recipe_data.get("description"),
            cook_time_minutes=int(_dec(recipe_data, "cook_time_minutes")),
            servings=int(_dec(recipe_data, "servings")),
            calories_kcal=_dec(recipe_data, "calories_kcal"),
            proteins_g=_dec(recipe_data, "proteins_g"),
            fats_g=_dec(recipe_data, "fats_g"),
            carbs_g=_dec(recipe_data, "carbs_g"),
            status="approved",  # сид - витрина, модерация не нужна
        )
        session.add(recipe)

        recipe.categories = [categories[name] for name in recipe_data.get("categories", [])]
        recipe.diets = [diets[name] for name in recipe_data.get("diets", [])]
        recipe.steps = [
            RecipeStep(step_number=number, instruction=step.strip())
            for number, step in enumerate(recipe_data["steps"], start=1)
        ]

        for item in recipe_data.get("ingredients", []):
            name = item["name"].strip()
            ingredient = ingredients.get(name)
            if ingredient is None:
                ingredient = await session.scalar(select(Ingredient).where(Ingredient.name == name))
                if ingredient is None:
                    ingredient = Ingredient(name=name)
                    session.add(ingredient)
                    created_ingredients += 1
                ingredients[name] = ingredient
            quantity = item.get("quantity")
            session.add(
                RecipeIngredient(
                    recipe=recipe,
                    ingredient=ingredient,
                    quantity=_dec(item, "quantity") if quantity is not None else None,
                    unit=item.get("unit"),
                )
            )

        loaded.append(recipe.title)

    await session.commit()
    for title in loaded:
        print(f"[+] {title}")
    print(f"Готово: {len(loaded)} рецепт(ов), новых ингредиентов: {created_ingredients}.")
    return len(loaded)


async def run(data_path: Path = DATA_PATH) -> int:
    """Точка входа: .env -> движок -> сессия -> seed."""
    load_dotenv()
    url = os.getenv("DATABASE_URL", DEFAULT_URL)

    engine = make_engine(url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            return await seed(session, _load(data_path))
    finally:
        await engine.dispose()


def main() -> int:
    try:
        asyncio.run(run())
    except SeedDataError as exc:
        print(f"Ошибка сида: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
