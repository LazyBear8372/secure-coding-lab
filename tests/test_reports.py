import re
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from secure_coding_lab.models import Product, Report, ReportStatus, User
from secure_coding_lab.security import hash_password

PASSWORD = "correct horse battery staple"


def csrf_token(response_text: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


async def create_user(database_factory: async_sessionmaker[AsyncSession], username: str) -> UUID:
    async with database_factory() as database:
        user = User(username=username, password_hash=hash_password(PASSWORD))
        database.add(user)
        await database.commit()
        return user.id


async def create_product(
    database_factory: async_sessionmaker[AsyncSession], seller_id: UUID
) -> UUID:
    async with database_factory() as database:
        product = Product(
            seller_id=seller_id,
            name="신고 대상 상품",
            description="신고 기능 테스트 상품",
            price=1000,
            image_key=f"{uuid4().hex}.png",
        )
        database.add(product)
        await database.commit()
        return product.id


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


@pytest.mark.asyncio
async def test_user_report_is_created_and_visible_in_my_reports(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    reporter_id = await create_user(database_factory, "reporter")
    target_id = await create_user(database_factory, "target")
    await login(client, "reporter")
    page = await client.get("/users/target/report")

    response = await client.post(
        "/users/target/reports",
        data={
            "reason": "  반복적으로 부적절한 메시지를 전송했습니다.  ",
            "csrf_token": csrf_token(page.text),
        },
    )

    assert response.status_code == 303
    async with database_factory() as database:
        report = (await database.execute(select(Report))).scalar_one()
    assert report.reporter_id == reporter_id
    assert report.target_user_id == target_id
    assert report.target_product_id is None
    assert report.status == ReportStatus.PENDING
    assert report.reason == "반복적으로 부적절한 메시지를 전송했습니다."
    history = await client.get("/me/reports")
    assert "검토 대기" in history.text
    assert "target" in history.text


@pytest.mark.asyncio
async def test_product_report_is_created_and_owner_cannot_report_own_product(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = await create_user(database_factory, "seller")
    await create_user(database_factory, "buyer")
    product_id = await create_product(database_factory, seller_id)

    await login(client, "seller")
    assert (await client.get(f"/products/{product_id}/report")).status_code == 404

    await login(client, "buyer")
    page = await client.get(f"/products/{product_id}/report")
    response = await client.post(
        f"/products/{product_id}/reports",
        data={
            "reason": "상품 설명과 실제 상태가 명확하게 다릅니다.",
            "csrf_token": csrf_token(page.text),
        },
    )
    assert response.status_code == 303
    async with database_factory() as database:
        report = (await database.execute(select(Report))).scalar_one()
    assert report.target_product_id == product_id
    assert report.target_user_id is None


@pytest.mark.asyncio
async def test_self_report_and_forged_csrf_are_rejected(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_user(database_factory, "reporter")
    await create_user(database_factory, "target")
    await login(client, "reporter")
    assert (await client.get("/users/reporter/report")).status_code == 404

    response = await client.post(
        "/users/target/reports",
        data={"reason": "충분히 긴 신고 사유입니다.", "csrf_token": "forged"},
    )
    assert response.status_code == 403
    async with database_factory() as database:
        assert (await database.execute(select(Report))).scalars().all() == []


@pytest.mark.asyncio
async def test_report_reason_length_and_duplicate_are_rejected(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_user(database_factory, "reporter")
    await create_user(database_factory, "target")
    await login(client, "reporter")
    page = await client.get("/users/target/report")
    short = await client.post(
        "/users/target/reports",
        data={"reason": "짧음", "csrf_token": csrf_token(page.text)},
    )
    assert short.status_code == 400

    page = await client.get("/users/target/report")
    payload = {
        "reason": "중복 신고를 검증하기 위한 충분한 사유입니다.",
        "csrf_token": csrf_token(page.text),
    }
    assert (await client.post("/users/target/reports", data=payload)).status_code == 303
    page = await client.get("/users/target/report")
    payload["csrf_token"] = csrf_token(page.text)
    duplicate = await client.post("/users/target/reports", data=payload)
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_database_rejects_duplicate_active_reports(
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    reporter_id = await create_user(database_factory, "reporter")
    target_id = await create_user(database_factory, "target")
    async with database_factory() as database:
        database.add_all(
            [
                Report(
                    reporter_id=reporter_id,
                    target_user_id=target_id,
                    reason="첫 번째 신고 사유입니다.",
                ),
                Report(
                    reporter_id=reporter_id,
                    target_user_id=target_id,
                    reason="두 번째 신고 사유입니다.",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await database.commit()
