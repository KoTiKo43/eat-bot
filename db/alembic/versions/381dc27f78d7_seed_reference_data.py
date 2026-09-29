"""seed reference data

Revision ID: 381dc27f78d7
Revises: 10826df62dd3
Create Date: 2026-09-29 20:17:52.617666

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "381dc27f78d7"
down_revision: Union[str, Sequence[str], None] = "10826df62dd3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORIES = ("Завтрак", "Обед", "Ужин", "Перекус")

DIETS = ("Вегетарианское", "Веганское", "Без глютена", "Кето")


def upgrade() -> None:
    """Наполняем справочники эталонными значениями."""
    categories = sa.table("categories", sa.column("name", sa.String))
    op.bulk_insert(categories, [{"name": name} for name in CATEGORIES])

    diets = sa.table("diets", sa.column("name", sa.String))
    op.bulk_insert(diets, [{"name": name} for name in DIETS])


def downgrade() -> None:
    """Чистим справочники.

    RESTRICT из recipe_categories/recipe_diets не даст удалить значения,
    которые уже используются рецептами, — это защита схемы, а не баг.
    """
    op.execute("DELETE FROM diets")
    op.execute("DELETE FROM categories")
