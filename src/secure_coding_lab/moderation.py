from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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


async def apply_automatic_block(
    database: AsyncSession,
    report: Report,
    settings: Settings,
) -> bool:
    if report.status != ReportStatus.ACCEPTED:
        return False

    now = datetime.now(UTC)
    if report.target_user_id is not None:
        target = await database.scalar(
            select(User).where(User.id == report.target_user_id).with_for_update()
        )
        if target is None or target.status != UserStatus.ACTIVE:
            return False
        accepted_count = await database.scalar(
            select(func.count(Report.id)).where(
                Report.target_user_id == target.id,
                Report.status == ReportStatus.ACCEPTED,
            )
        )
        if accepted_count < settings.user_report_block_threshold:
            return False
        target.status = UserStatus.SUSPENDED
        target.suspended_at = now
        target.updated_at = now
        await database.execute(
            update(Session)
            .where(Session.user_id == target.id, Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return True

    if report.target_product_id is not None:
        target = await database.scalar(
            select(Product).where(Product.id == report.target_product_id).with_for_update()
        )
        if target is None or target.status not in (
            ProductStatus.ACTIVE,
            ProductStatus.SOLD,
        ):
            return False
        accepted_count = await database.scalar(
            select(func.count(Report.id)).where(
                Report.target_product_id == target.id,
                Report.status == ReportStatus.ACCEPTED,
            )
        )
        if accepted_count < settings.product_report_block_threshold:
            return False
        target.status = ProductStatus.BLOCKED
        target.blocked_at = now
        target.updated_at = now
        return True

    return False
