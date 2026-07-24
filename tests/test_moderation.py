from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from secure_coding_lab.config import Settings
from secure_coding_lab.models import (
    Product,
    ProductStatus,
    Report,
    ReportStatus,
    Session,
    User,
    UserStatus,
)
from secure_coding_lab.moderation import apply_automatic_block
from secure_coding_lab.security import hash_password

PASSWORD = "correct horse battery staple"


def settings() -> Settings:
    return Settings(
        app_env="test",
        secret_key="test-secret-key-with-at-least-32-characters",
        database_url="sqlite+aiosqlite://",
        user_report_block_threshold=3,
        product_report_block_threshold=3,
    )


async def create_user(database_factory: async_sessionmaker[AsyncSession], username: str) -> UUID:
    async with database_factory() as database:
        user = User(username=username, password_hash=hash_password(PASSWORD))
        database.add(user)
        await database.commit()
        return user.id


async def accepted_user_reports(
    database_factory: async_sessionmaker[AsyncSession],
    target_id: UUID,
    count: int,
) -> list[UUID]:
    reporter_ids = [
        await create_user(database_factory, f"reporter{uuid4().hex[:8]}") for _ in range(count)
    ]
    async with database_factory() as database:
        reports = [
            Report(
                reporter_id=reporter_id,
                target_user_id=target_id,
                reason="자동 차단 기준 검증을 위한 신고입니다.",
                status=ReportStatus.ACCEPTED,
            )
            for reporter_id in reporter_ids
        ]
        database.add_all(reports)
        await database.commit()
        return [report.id for report in reports]


@pytest.mark.asyncio
async def test_user_is_blocked_at_threshold_and_sessions_are_revoked(
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    target_id = await create_user(database_factory, "target")
    report_ids = await accepted_user_reports(database_factory, target_id, 3)
    async with database_factory() as database:
        session = Session(
            user_id=target_id,
            token_hash="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        database.add(session)
        await database.commit()
        report = await database.get(Report, report_ids[-1])
        assert report is not None
        assert await apply_automatic_block(database, report, settings())
        await database.commit()

    async with database_factory() as database:
        target = await database.get(User, target_id)
        stored_session = (await database.execute(select(Session))).scalar_one()
    assert target is not None and target.status == UserStatus.SUSPENDED
    assert target.suspended_at is not None
    assert stored_session.revoked_at is not None


@pytest.mark.asyncio
async def test_user_is_not_blocked_below_threshold(
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    target_id = await create_user(database_factory, "target")
    report_ids = await accepted_user_reports(database_factory, target_id, 2)
    async with database_factory() as database:
        report = await database.get(Report, report_ids[-1])
        assert report is not None
        assert not await apply_automatic_block(database, report, settings())
        await database.commit()
    async with database_factory() as database:
        target = await database.get(User, target_id)
    assert target is not None and target.status == UserStatus.ACTIVE


@pytest.mark.asyncio
async def test_product_is_blocked_at_threshold_and_reapply_is_idempotent(
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = await create_user(database_factory, "seller")
    reporter_ids = [await create_user(database_factory, f"reporter{index}") for index in range(3)]
    async with database_factory() as database:
        product = Product(
            seller_id=seller_id,
            name="자동 차단 대상 상품",
            description="자동 차단 검증",
            price=1000,
            image_key=f"{uuid4().hex}.png",
        )
        database.add(product)
        await database.flush()
        reports = [
            Report(
                reporter_id=reporter_id,
                target_product_id=product.id,
                reason="자동 차단 기준 검증을 위한 신고입니다.",
                status=ReportStatus.ACCEPTED,
            )
            for reporter_id in reporter_ids
        ]
        database.add_all(reports)
        await database.commit()
        report = reports[-1]
        assert await apply_automatic_block(database, report, settings())
        assert not await apply_automatic_block(database, report, settings())
        await database.commit()
        product_id = product.id

    async with database_factory() as database:
        product = await database.get(Product, product_id)
    assert product is not None and product.status == ProductStatus.BLOCKED
    assert product.blocked_at is not None


@pytest.mark.asyncio
async def test_rejected_report_never_triggers_block(
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    target_id = await create_user(database_factory, "target")
    reporter_id = await create_user(database_factory, "reporter")
    async with database_factory() as database:
        report = Report(
            reporter_id=reporter_id,
            target_user_id=target_id,
            reason="거절된 신고는 자동 차단에서 제외됩니다.",
            status=ReportStatus.REJECTED,
        )
        database.add(report)
        await database.commit()
        assert not await apply_automatic_block(database, report, settings())
