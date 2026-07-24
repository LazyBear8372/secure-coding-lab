import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from secure_coding_lab.models import (
    AdminAuditLog,
    Product,
    ProductStatus,
    Report,
    ReportStatus,
    Session,
    User,
    UserRole,
    UserStatus,
)
from secure_coding_lab.security import hash_password

PASSWORD = "correct horse battery staple"


def csrf_token(response_text: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


async def create_user(
    database_factory: async_sessionmaker[AsyncSession],
    username: str,
    *,
    role: UserRole = UserRole.USER,
) -> UUID:
    async with database_factory() as database:
        user = User(
            username=username,
            password_hash=hash_password(PASSWORD),
            role=role,
        )
        database.add(user)
        await database.commit()
        return user.id


async def login(client: AsyncClient, username: str) -> None:
    page = await client.get("/login")
    response = await client.post(
        "/login",
        data={
            "username": username,
            "password": PASSWORD,
            "csrf_token": csrf_token(page.text),
        },
    )
    assert response.status_code == 303
    client.cookies.update(response.cookies)


async def create_pending_user_reports(
    database_factory: async_sessionmaker[AsyncSession],
    target_id: UUID,
    count: int,
) -> list[UUID]:
    reporter_ids = [
        await create_user(database_factory, f"reporter{index}") for index in range(count)
    ]
    async with database_factory() as database:
        reports = [
            Report(
                reporter_id=reporter_id,
                target_user_id=target_id,
                reason="관리자 신고 심사를 검증하기 위한 충분한 사유입니다.",
            )
            for reporter_id in reporter_ids
        ]
        database.add_all(reports)
        await database.commit()
        return [report.id for report in reports]


@pytest.mark.asyncio
async def test_admin_pages_require_admin_role(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert (await client.get("/admin")).headers["location"] == "/login"
    await create_user(database_factory, "ordinary")
    await login(client, "ordinary")
    for path in (
        "/admin",
        "/admin/reports",
        "/admin/users",
        "/admin/products",
        "/admin/audit",
    ):
        response = await client.get(path)
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_accepts_reports_and_triggers_automatic_suspension(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_id = await create_user(database_factory, "admin", role=UserRole.ADMIN)
    target_id = await create_user(database_factory, "target")
    report_ids = await create_pending_user_reports(database_factory, target_id, 3)
    async with database_factory() as database:
        database.add(
            Session(
                user_id=target_id,
                token_hash="b" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await database.commit()
    await login(client, "admin")

    for report_id in report_ids:
        page = await client.get("/admin/reports")
        response = await client.post(
            f"/admin/reports/{report_id}/review",
            data={
                "decision": "accepted",
                "reason": "증거를 검토하여 신고를 승인합니다.",
                "csrf_token": csrf_token(page.text),
            },
        )
        assert response.status_code == 303

    async with database_factory() as database:
        reports = (await database.execute(select(Report))).scalars().all()
        target = await database.get(User, target_id)
        target_session = (
            await database.execute(select(Session).where(Session.user_id == target_id))
        ).scalar_one()
        audit_count = await database.scalar(select(func.count(AdminAuditLog.id)))
    assert all(report.status == ReportStatus.ACCEPTED for report in reports)
    assert all(report.reviewed_by_id == admin_id for report in reports)
    assert all(report.reviewed_at is not None for report in reports)
    assert target is not None and target.status == UserStatus.SUSPENDED
    assert target_session.revoked_at is not None
    assert audit_count == 4


@pytest.mark.asyncio
async def test_report_cannot_be_reviewed_twice_and_reject_does_not_block(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_user(database_factory, "admin", role=UserRole.ADMIN)
    target_id = await create_user(database_factory, "target")
    report_id = (await create_pending_user_reports(database_factory, target_id, 1))[0]
    await login(client, "admin")
    page = await client.get("/admin/reports")
    payload = {
        "decision": "rejected",
        "reason": "신고 근거가 충분하지 않아 거절합니다.",
        "csrf_token": csrf_token(page.text),
    }
    assert (
        await client.post(f"/admin/reports/{report_id}/review", data=payload)
    ).status_code == 303

    page = await client.get("/admin/reports")
    payload["csrf_token"] = csrf_token(page.text)
    duplicate = await client.post(f"/admin/reports/{report_id}/review", data=payload)
    assert duplicate.status_code == 409
    async with database_factory() as database:
        target = await database.get(User, target_id)
        report = await database.get(Report, report_id)
    assert target is not None and target.status == UserStatus.ACTIVE
    assert report is not None and report.status == ReportStatus.REJECTED


@pytest.mark.asyncio
async def test_admin_suspends_and_restores_user_with_session_revocation(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_id = await create_user(database_factory, "admin", role=UserRole.ADMIN)
    target_id = await create_user(database_factory, "target")
    async with database_factory() as database:
        database.add(
            Session(
                user_id=target_id,
                token_hash="c" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await database.commit()
    await login(client, "admin")

    page = await client.get("/admin/users")
    suspended = await client.post(
        f"/admin/users/{target_id}/status",
        data={
            "action": "suspend",
            "reason": "관리자 검토에 따른 계정 정지입니다.",
            "csrf_token": csrf_token(page.text),
        },
    )
    assert suspended.status_code == 303
    page = await client.get("/admin/users")
    restored = await client.post(
        f"/admin/users/{target_id}/status",
        data={
            "action": "restore",
            "reason": "재검토 결과 계정을 복구합니다.",
            "csrf_token": csrf_token(page.text),
        },
    )
    assert restored.status_code == 303

    page = await client.get("/admin/users")
    self_change = await client.post(
        f"/admin/users/{admin_id}/status",
        data={
            "action": "suspend",
            "reason": "자기 상태 변경 시도를 검증합니다.",
            "csrf_token": csrf_token(page.text),
        },
    )
    assert self_change.status_code == 400
    async with database_factory() as database:
        target = await database.get(User, target_id)
        target_session = (
            await database.execute(select(Session).where(Session.user_id == target_id))
        ).scalar_one()
        actions = (
            (
                await database.execute(
                    select(AdminAuditLog.action).order_by(AdminAuditLog.created_at)
                )
            )
            .scalars()
            .all()
        )
    assert target is not None and target.status == UserStatus.ACTIVE
    assert target_session.revoked_at is not None
    assert actions == ["user.suspend", "user.restore"]


@pytest.mark.asyncio
async def test_admin_deletes_product_and_records_escaped_audit_reason(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_user(database_factory, "admin", role=UserRole.ADMIN)
    seller_id = await create_user(database_factory, "seller")
    async with database_factory() as database:
        product = Product(
            seller_id=seller_id,
            name="관리자 삭제 대상",
            description="관리자 상품 삭제 테스트",
            price=1000,
            image_key=f"{uuid4().hex}.png",
        )
        database.add(product)
        await database.commit()
        product_id = product.id
    await login(client, "admin")
    page = await client.get("/admin/products")
    response = await client.post(
        f"/admin/products/{product_id}/delete",
        data={
            "reason": "<script>alert(1)</script> 관리자 삭제",
            "csrf_token": csrf_token(page.text),
        },
    )
    assert response.status_code == 303

    async with database_factory() as database:
        product = await database.get(Product, product_id)
        audit = (await database.execute(select(AdminAuditLog))).scalar_one()
    assert product is not None and product.status == ProductStatus.DELETED
    assert product.deleted_at is not None
    assert audit.action == "product.delete"
    audit_page = await client.get("/admin/audit")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in audit_page.text
    assert "<script>alert(1)</script>" not in audit_page.text


@pytest.mark.asyncio
async def test_admin_mutations_reject_forged_csrf(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_user(database_factory, "admin", role=UserRole.ADMIN)
    target_id = await create_user(database_factory, "target")
    await login(client, "admin")
    response = await client.post(
        f"/admin/users/{target_id}/status",
        data={
            "action": "suspend",
            "reason": "위조 요청 검증을 위한 사유입니다.",
            "csrf_token": "forged",
        },
    )
    assert response.status_code == 403
    async with database_factory() as database:
        target = await database.get(User, target_id)
        logs = (await database.execute(select(AdminAuditLog))).scalars().all()
    assert target is not None and target.status == UserStatus.ACTIVE
    assert logs == []
