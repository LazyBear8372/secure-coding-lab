import re
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from secure_coding_lab.models import Wallet, WalletTransfer, WalletTransferType

PASSWORD = "correct horse battery staple"


def csrf_token(response_text: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def idempotency_key(response_text: str, action: str) -> str:
    match = re.search(
        rf'action="/wallet/{action}"[\s\S]*?name="idempotency_key" value="([^"]+)"',
        response_text,
    )
    assert match is not None
    return match.group(1)


async def signup_and_login(client: AsyncClient, username: str = "walletuser") -> None:
    signup_page = await client.get("/signup")
    response = await client.post(
        "/signup",
        data={
            "username": username,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "csrf_token": csrf_token(signup_page.text),
        },
    )
    assert response.status_code == 303
    login_page = await client.get("/login")
    response = await client.post(
        "/login",
        data={
            "username": username,
            "password": PASSWORD,
            "csrf_token": csrf_token(login_page.text),
        },
    )
    client.cookies.update(response.cookies)


@pytest.mark.asyncio
async def test_wallet_is_created_at_signup_and_requires_login(
    client: AsyncClient, database_factory: async_sessionmaker[AsyncSession]
) -> None:
    assert (await client.get("/wallet")).headers["location"] == "/login"
    await signup_and_login(client)
    page = await client.get("/wallet")
    assert page.status_code == 200
    assert "0원" in page.text
    async with database_factory() as database:
        wallet = (await database.execute(select(Wallet))).scalar_one()
    assert wallet.balance == 0


@pytest.mark.asyncio
async def test_deposit_is_idempotent_and_records_history(
    client: AsyncClient, database_factory: async_sessionmaker[AsyncSession]
) -> None:
    await signup_and_login(client)
    page = await client.get("/wallet")
    payload = {
        "amount": "25000",
        "csrf_token": csrf_token(page.text),
        "idempotency_key": idempotency_key(page.text, "deposit"),
    }
    first = await client.post("/wallet/deposit", data=payload)
    second = await client.post("/wallet/deposit", data=payload)
    assert first.status_code == second.status_code == 303
    async with database_factory() as database:
        wallet = (await database.execute(select(Wallet))).scalar_one()
        transfers = (await database.execute(select(WalletTransfer))).scalars().all()
    assert wallet.balance == 25000
    assert len(transfers) == 1
    assert transfers[0].type == WalletTransferType.DEPOSIT


@pytest.mark.asyncio
async def test_withdrawal_rejects_insufficient_balance_and_invalid_inputs(
    client: AsyncClient,
) -> None:
    await signup_and_login(client)
    page = await client.get("/wallet")
    response = await client.post(
        "/wallet/withdraw",
        data={
            "amount": "1",
            "csrf_token": csrf_token(page.text),
            "idempotency_key": idempotency_key(page.text, "withdraw"),
        },
    )
    assert response.status_code == 400
    assert "잔액이 부족합니다" in response.text

    page = await client.get("/wallet")
    response = await client.post(
        "/wallet/deposit",
        data={
            "amount": "1.5",
            "csrf_token": csrf_token(page.text),
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 400
    assert "금액은 1 이상의 정수" in response.text


@pytest.mark.asyncio
async def test_withdrawal_updates_balance_and_rejects_forged_csrf(client: AsyncClient) -> None:
    await signup_and_login(client)
    page = await client.get("/wallet")
    await client.post(
        "/wallet/deposit",
        data={
            "amount": "30000",
            "csrf_token": csrf_token(page.text),
            "idempotency_key": idempotency_key(page.text, "deposit"),
        },
    )
    page = await client.get("/wallet")
    response = await client.post(
        "/wallet/withdraw",
        data={
            "amount": "12000",
            "csrf_token": csrf_token(page.text),
            "idempotency_key": idempotency_key(page.text, "withdraw"),
        },
    )
    assert response.status_code == 303
    page = await client.get("/wallet")
    assert "18,000원" in page.text
    assert "입금" in page.text and "출금" in page.text

    response = await client.post(
        "/wallet/withdraw",
        data={"amount": "1", "csrf_token": "forged", "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 403
