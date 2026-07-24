"""Add report review metadata and admin audit logs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("reviewed_by_id", sa.Uuid(), nullable=True))
    op.add_column("reports", sa.Column("review_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_reports_reviewed_by_id_users",
        "reports",
        "users",
        ["reviewed_by_id"],
        ["id"],
    )
    op.create_index("ix_reports_reviewed_by_id", "reports", ["reviewed_by_id"], unique=False)
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admin_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=True),
        sa.Column("target_user_id", sa.Uuid(), nullable=True),
        sa.Column("target_product_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "report_id IS NOT NULL OR target_user_id IS NOT NULL OR target_product_id IS NOT NULL",
            name="ck_admin_audit_logs_has_target",
        ),
        sa.ForeignKeyConstraint(["admin_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "admin_id",
        "action",
        "report_id",
        "target_user_id",
        "target_product_id",
        "created_at",
    ):
        op.create_index(
            f"ix_admin_audit_logs_{column}",
            "admin_audit_logs",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("admin_audit_logs")
    op.drop_index("ix_reports_reviewed_by_id", table_name="reports")
    op.drop_constraint("fk_reports_reviewed_by_id_users", "reports", type_="foreignkey")
    op.drop_column("reports", "review_reason")
    op.drop_column("reports", "reviewed_by_id")
