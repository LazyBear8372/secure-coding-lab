"""Enforce reusable global and product chat rooms.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_rooms", sa.Column("buyer_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_chat_rooms_buyer_id_users",
        "chat_rooms",
        "users",
        ["buyer_id"],
        ["id"],
    )
    op.create_index("ix_chat_rooms_buyer_id", "chat_rooms", ["buyer_id"], unique=False)
    op.drop_constraint("ck_chat_rooms_type_product", "chat_rooms", type_="check")
    op.create_check_constraint(
        "ck_chat_rooms_type_participants",
        "chat_rooms",
        "(type = 'global' AND product_id IS NULL AND buyer_id IS NULL) OR "
        "(type = 'product' AND product_id IS NOT NULL AND buyer_id IS NOT NULL)",
    )
    op.create_index(
        "uq_chat_rooms_product_buyer",
        "chat_rooms",
        ["product_id", "buyer_id"],
        unique=True,
    )
    op.create_index(
        "uq_chat_rooms_global",
        "chat_rooms",
        ["type"],
        unique=True,
        postgresql_where=sa.text("type = 'global'"),
    )


def downgrade() -> None:
    op.drop_index("uq_chat_rooms_global", table_name="chat_rooms")
    op.drop_index("uq_chat_rooms_product_buyer", table_name="chat_rooms")
    op.drop_constraint("ck_chat_rooms_type_participants", "chat_rooms", type_="check")
    op.create_check_constraint(
        "ck_chat_rooms_type_product",
        "chat_rooms",
        "(type = 'global' AND product_id IS NULL) OR (type = 'product' AND product_id IS NOT NULL)",
    )
    op.drop_index("ix_chat_rooms_buyer_id", table_name="chat_rooms")
    op.drop_constraint("fk_chat_rooms_buyer_id_users", "chat_rooms", type_="foreignkey")
    op.drop_column("chat_rooms", "buyer_id")
