from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from secure_coding_lab.db import Base


def enum_type[EnumT: StrEnum](enum_class: type[EnumT], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    WITHDRAWN = "withdrawn"


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class ProductStatus(StrEnum):
    ACTIVE = "active"
    SOLD = "sold"
    BLOCKED = "blocked"
    DELETED = "deleted"


class ChatRoomType(StrEnum):
    GLOBAL = "global"
    PRODUCT = "product"


class ReportStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class WalletTransferType(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(
        enum_type(UserStatus, "user_status"),
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
        index=True,
    )
    role: Mapped[UserRole] = mapped_column(
        enum_type(UserRole, "user_role"),
        default=UserRole.USER,
        server_default=UserRole.USER.value,
    )
    bio: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    seller_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[int] = mapped_column(BigInteger)
    image_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[ProductStatus] = mapped_column(
        enum_type(ProductStatus, "product_status"),
        default=ProductStatus.ACTIVE,
        server_default=ProductStatus.ACTIVE.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatRoom(Base):
    __tablename__ = "chat_rooms"
    __table_args__ = (
        CheckConstraint(
            "(type = 'global' AND product_id IS NULL) OR "
            "(type = 'product' AND product_id IS NOT NULL)",
            name="ck_chat_rooms_type_product",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    type: Mapped[ChatRoomType] = mapped_column(enum_type(ChatRoomType, "chat_room_type"))
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )


class ChatRoomMember(Base):
    __tablename__ = "chat_room_members"

    chat_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_rooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    chat_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_rooms.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(
            "(target_user_id IS NOT NULL AND target_product_id IS NULL) OR "
            "(target_user_id IS NULL AND target_product_id IS NOT NULL)",
            name="ck_reports_exactly_one_target",
        ),
        Index(
            "uq_reports_active_user_target",
            "reporter_id",
            "target_user_id",
            unique=True,
            postgresql_where=text(
                "target_user_id IS NOT NULL AND status IN ('pending', 'accepted')"
            ),
            sqlite_where=text("target_user_id IS NOT NULL AND status IN ('pending', 'accepted')"),
        ),
        Index(
            "uq_reports_active_product_target",
            "reporter_id",
            "target_product_id",
            unique=True,
            postgresql_where=text(
                "target_product_id IS NOT NULL AND status IN ('pending', 'accepted')"
            ),
            sqlite_where=text(
                "target_product_id IS NOT NULL AND status IN ('pending', 'accepted')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    reporter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    target_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    target_product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[ReportStatus] = mapped_column(
        enum_type(ReportStatus, "report_status"),
        default=ReportStatus.PENDING,
        server_default=ReportStatus.PENDING.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_wallets_balance_nonnegative"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    balance: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class WalletTransfer(Base):
    __tablename__ = "wallet_transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_wallet_transfers_amount_positive"),
        CheckConstraint(
            "(type = 'deposit' AND chat_room_id IS NULL AND sender_wallet_id IS NULL "
            "AND receiver_wallet_id IS NOT NULL) OR "
            "(type = 'withdrawal' AND chat_room_id IS NULL AND sender_wallet_id IS NOT NULL "
            "AND receiver_wallet_id IS NULL) OR "
            "(type = 'transfer' AND chat_room_id IS NOT NULL AND sender_wallet_id IS NOT NULL "
            "AND receiver_wallet_id IS NOT NULL)",
            name="ck_wallet_transfers_type_participants",
        ),
        CheckConstraint(
            "sender_wallet_id IS NULL OR receiver_wallet_id IS NULL "
            "OR sender_wallet_id <> receiver_wallet_id",
            name="ck_wallet_transfers_distinct_wallets",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    chat_room_id: Mapped[UUID | None] = mapped_column(ForeignKey("chat_rooms.id"), index=True)
    sender_wallet_id: Mapped[UUID | None] = mapped_column(ForeignKey("wallets.id"), index=True)
    receiver_wallet_id: Mapped[UUID | None] = mapped_column(ForeignKey("wallets.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[WalletTransferType] = mapped_column(
        enum_type(WalletTransferType, "wallet_transfer_type")
    )
    idempotency_key: Mapped[UUID] = mapped_column(Uuid, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
