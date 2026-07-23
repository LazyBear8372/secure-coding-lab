"""Create the initial marketplace schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def enum_type(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            enum_type("active", "suspended", "withdrawn", name="user_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "role",
            enum_type("user", "admin", name="user_role"),
            server_default="user",
            nullable=False,
        ),
        sa.Column("bio", sa.Text(), server_default="", nullable=False),
        *timestamps(),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_status", "users", ["status"], unique=False)

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"], unique=False)

    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seller_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("image_key", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            enum_type("active", "sold", "blocked", "deleted", name="product_status"),
            server_default="active",
            nullable=False,
        ),
        *timestamps(),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
        sa.ForeignKeyConstraint(["seller_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_seller_id", "products", ["seller_id"], unique=False)
    op.create_index("ix_products_name", "products", ["name"], unique=False)
    op.create_index("ix_products_status", "products", ["status"], unique=False)

    op.create_table(
        "chat_rooms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", enum_type("global", "product", name="chat_room_type"), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(type = 'global' AND product_id IS NULL) OR "
            "(type = 'product' AND product_id IS NOT NULL)",
            name="ck_chat_rooms_type_product",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_rooms_product_id", "chat_rooms", ["product_id"], unique=False)

    op.create_table(
        "chat_room_members",
        sa.Column("chat_room_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["chat_room_id"], ["chat_rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_room_id", "user_id"),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_room_id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chat_room_id"], ["chat_rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_messages_chat_room_id", "chat_messages", ["chat_room_id"], unique=False
    )
    op.create_index("ix_chat_messages_sender_id", "chat_messages", ["sender_id"], unique=False)
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"], unique=False)

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=True),
        sa.Column("target_product_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            enum_type("pending", "accepted", "rejected", name="report_status"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(target_user_id IS NOT NULL AND target_product_id IS NULL) OR "
            "(target_user_id IS NULL AND target_product_id IS NOT NULL)",
            name="ck_reports_exactly_one_target",
        ),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reports_reporter_id", "reports", ["reporter_id"], unique=False)
    op.create_index("ix_reports_target_user_id", "reports", ["target_user_id"], unique=False)
    op.create_index("ix_reports_target_product_id", "reports", ["target_product_id"], unique=False)
    op.create_index("ix_reports_status", "reports", ["status"], unique=False)
    op.create_index(
        "uq_reports_active_user_target",
        "reports",
        ["reporter_id", "target_user_id"],
        unique=True,
        postgresql_where=sa.text(
            "target_user_id IS NOT NULL AND status IN ('pending', 'accepted')"
        ),
    )
    op.create_index(
        "uq_reports_active_product_target",
        "reports",
        ["reporter_id", "target_product_id"],
        unique=True,
        postgresql_where=sa.text(
            "target_product_id IS NOT NULL AND status IN ('pending', 'accepted')"
        ),
    )

    op.create_table(
        "wallets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("balance", sa.BigInteger(), server_default="0", nullable=False),
        *timestamps(),
        sa.CheckConstraint("balance >= 0", name="ck_wallets_balance_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "wallet_transfers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_room_id", sa.Uuid(), nullable=True),
        sa.Column("sender_wallet_id", sa.Uuid(), nullable=True),
        sa.Column("receiver_wallet_id", sa.Uuid(), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column(
            "type",
            enum_type("deposit", "withdrawal", "transfer", name="wallet_transfer_type"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_wallet_transfers_amount_positive"),
        sa.CheckConstraint(
            "(type = 'deposit' AND chat_room_id IS NULL AND sender_wallet_id IS NULL "
            "AND receiver_wallet_id IS NOT NULL) OR "
            "(type = 'withdrawal' AND chat_room_id IS NULL AND sender_wallet_id IS NOT NULL "
            "AND receiver_wallet_id IS NULL) OR "
            "(type = 'transfer' AND chat_room_id IS NOT NULL AND sender_wallet_id IS NOT NULL "
            "AND receiver_wallet_id IS NOT NULL)",
            name="ck_wallet_transfers_type_participants",
        ),
        sa.CheckConstraint(
            "sender_wallet_id IS NULL OR receiver_wallet_id IS NULL "
            "OR sender_wallet_id <> receiver_wallet_id",
            name="ck_wallet_transfers_distinct_wallets",
        ),
        sa.ForeignKeyConstraint(["chat_room_id"], ["chat_rooms.id"]),
        sa.ForeignKeyConstraint(["sender_wallet_id"], ["wallets.id"]),
        sa.ForeignKeyConstraint(["receiver_wallet_id"], ["wallets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_wallet_transfers_chat_room_id", "wallet_transfers", ["chat_room_id"], unique=False
    )
    op.create_index(
        "ix_wallet_transfers_sender_wallet_id",
        "wallet_transfers",
        ["sender_wallet_id"],
        unique=False,
    )
    op.create_index(
        "ix_wallet_transfers_receiver_wallet_id",
        "wallet_transfers",
        ["receiver_wallet_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("wallet_transfers")
    op.drop_table("wallets")
    op.drop_table("reports")
    op.drop_table("chat_messages")
    op.drop_table("chat_room_members")
    op.drop_table("chat_rooms")
    op.drop_table("products")
    op.drop_table("sessions")
    op.drop_table("users")
