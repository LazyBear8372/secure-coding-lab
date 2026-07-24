import asyncio
import re
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.db import Base, get_db_session
from secure_coding_lab.main import app
from secure_coding_lab.models import (
    ChatRoom,
    ChatRoomMember,
    ChatRoomType,
    Product,
    User,
    Wallet,
    WalletTransfer,
    WalletTransferType,
)
from secure_coding_lab.security import hash_password

PASSWORD = "correct horse battery staple"


def csrf_token(response_text: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def transfer_key(response_text: str) -> str:
    match = re.search(
        r'action="/chats/[^"]+/transfers"[\s\S]*?'
        r'name="idempotency_key" value="([^"]+)"',
        response_text,
    )
    assert match is not None
    return match.group(1)


async def create_user_with_wallet(
    database_factory: async_sessionmaker[AsyncSession],
    username: str,
    *,
    balance: int = 0,
) -> tuple[UUID, UUID]:
    async with database_factory() as database:
        user = User(username=username, password_hash=hash_password(PASSWORD))
        database.add(user)
        await database.flush()
        wallet = Wallet(user_id=user.id, balance=balance)
        database.add(wallet)
        await database.commit()
        return user.id, wallet.id


async def create_product_room(
    database_factory: async_sessionmaker[AsyncSession],
    seller_id: UUID,
    buyer_id: UUID,
) -> UUID:
    async with database_factory() as database:
        product = Product(
            seller_id=seller_id,
            name="송금 테스트 상품",
            description="안전한 송금 테스트",
            price=50000,
            image_key=f"{uuid4().hex}.png",
        )
        database.add(product)
        await database.flush()
        room = ChatRoom(
            type=ChatRoomType.PRODUCT,
            product_id=product.id,
            buyer_id=buyer_id,
        )
        database.add(room)
        await database.flush()
        database.add_all(
            [
                ChatRoomMember(chat_room_id=room.id, user_id=seller_id),
                ChatRoomMember(chat_room_id=room.id, user_id=buyer_id),
            ]
        )
        await database.commit()
        return room.id


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
async def test_buyer_transfers_to_seller_once_for_repeated_request(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id, seller_wallet_id = await create_user_with_wallet(
        database_factory, "seller", balance=100
    )
    buyer_id, buyer_wallet_id = await create_user_with_wallet(
        database_factory, "buyer", balance=50000
    )
    room_id = await create_product_room(database_factory, seller_id, buyer_id)
    await login(client, "buyer")

    page = await client.get(f"/chats/{room_id}")
    payload = {
        "amount": "10000",
        "csrf_token": csrf_token(page.text),
        "idempotency_key": transfer_key(page.text),
    }
    first = await client.post(f"/chats/{room_id}/transfers", data=payload)
    second = await client.post(f"/chats/{room_id}/transfers", data=payload)

    assert first.status_code == second.status_code == 303
    assert first.headers["location"] == f"/chats/{room_id}?transferred=1"
    async with database_factory() as database:
        buyer_wallet = await database.get(Wallet, buyer_wallet_id)
        seller_wallet = await database.get(Wallet, seller_wallet_id)
        transfers = (await database.execute(select(WalletTransfer))).scalars().all()
    assert buyer_wallet is not None and buyer_wallet.balance == 40000
    assert seller_wallet is not None and seller_wallet.balance == 10100
    assert len(transfers) == 1
    assert transfers[0].type == WalletTransferType.TRANSFER
    assert transfers[0].chat_room_id == room_id


@pytest.mark.asyncio
async def test_insufficient_balance_rolls_back_entire_transfer(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id, seller_wallet_id = await create_user_with_wallet(
        database_factory, "seller", balance=50
    )
    buyer_id, buyer_wallet_id = await create_user_with_wallet(
        database_factory, "buyer", balance=100
    )
    room_id = await create_product_room(database_factory, seller_id, buyer_id)
    await login(client, "buyer")
    page = await client.get(f"/chats/{room_id}")

    response = await client.post(
        f"/chats/{room_id}/transfers",
        data={
            "amount": "101",
            "csrf_token": csrf_token(page.text),
            "idempotency_key": transfer_key(page.text),
        },
    )

    assert response.status_code == 400
    assert "잔액이 부족합니다" in response.text
    async with database_factory() as database:
        buyer_wallet = await database.get(Wallet, buyer_wallet_id)
        seller_wallet = await database.get(Wallet, seller_wallet_id)
        transfers = (await database.execute(select(WalletTransfer))).scalars().all()
    assert buyer_wallet is not None and buyer_wallet.balance == 100
    assert seller_wallet is not None and seller_wallet.balance == 50
    assert transfers == []


@pytest.mark.asyncio
async def test_seller_and_outsider_cannot_transfer_from_product_chat(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id, _ = await create_user_with_wallet(database_factory, "seller")
    buyer_id, _ = await create_user_with_wallet(database_factory, "buyer", balance=1000)
    await create_user_with_wallet(database_factory, "outsider", balance=1000)
    room_id = await create_product_room(database_factory, seller_id, buyer_id)

    await login(client, "seller")
    seller_page = await client.get(f"/chats/{room_id}")
    seller_response = await client.post(
        f"/chats/{room_id}/transfers",
        data={
            "amount": "1",
            "csrf_token": csrf_token(seller_page.text),
            "idempotency_key": str(uuid4()),
        },
    )
    assert seller_response.status_code == 404

    await login(client, "outsider")
    page = await client.get("/chats")
    outsider_response = await client.post(
        f"/chats/{room_id}/transfers",
        data={
            "amount": "1",
            "csrf_token": csrf_token(page.text),
            "idempotency_key": str(uuid4()),
        },
    )
    assert outsider_response.status_code == 404


@pytest.mark.asyncio
async def test_transfer_rejects_forged_csrf_and_invalid_amount(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id, _ = await create_user_with_wallet(database_factory, "seller")
    buyer_id, _ = await create_user_with_wallet(database_factory, "buyer", balance=1000)
    room_id = await create_product_room(database_factory, seller_id, buyer_id)
    await login(client, "buyer")

    forged = await client.post(
        f"/chats/{room_id}/transfers",
        data={
            "amount": "1",
            "csrf_token": "forged",
            "idempotency_key": str(uuid4()),
        },
    )
    assert forged.status_code == 403

    page = await client.get(f"/chats/{room_id}")
    invalid = await client.post(
        f"/chats/{room_id}/transfers",
        data={
            "amount": "1.5",
            "csrf_token": csrf_token(page.text),
            "idempotency_key": transfer_key(page.text),
        },
    )
    assert invalid.status_code == 400
    assert "1 이상의 정수" in invalid.text


@pytest.mark.asyncio
async def test_concurrent_transfers_cannot_overdraw_buyer(
    tmp_path,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'transfer-concurrency.db'}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    database_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_database():
        async with database_factory() as database:
            yield database

    app.dependency_overrides[get_db_session] = override_database
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="test",
        secret_key="test-secret-key-with-at-least-32-characters",
        database_url="sqlite+aiosqlite://",
        upload_dir=str(tmp_path / "uploads"),
    )
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            seller_id, seller_wallet_id = await create_user_with_wallet(database_factory, "seller")
            buyer_id, buyer_wallet_id = await create_user_with_wallet(
                database_factory, "buyer", balance=100
            )
            room_id = await create_product_room(database_factory, seller_id, buyer_id)
            await login(client, "buyer")
            page = await client.get(f"/chats/{room_id}")
            csrf = csrf_token(page.text)

            responses = await asyncio.gather(
                client.post(
                    f"/chats/{room_id}/transfers",
                    data={
                        "amount": "80",
                        "csrf_token": csrf,
                        "idempotency_key": str(uuid4()),
                    },
                ),
                client.post(
                    f"/chats/{room_id}/transfers",
                    data={
                        "amount": "80",
                        "csrf_token": csrf,
                        "idempotency_key": str(uuid4()),
                    },
                ),
            )

            assert sorted(response.status_code for response in responses) == [303, 400]
            async with database_factory() as database:
                buyer_wallet = await database.get(Wallet, buyer_wallet_id)
                seller_wallet = await database.get(Wallet, seller_wallet_id)
                transfers = (await database.execute(select(WalletTransfer))).scalars().all()
            assert buyer_wallet is not None and buyer_wallet.balance == 20
            assert seller_wallet is not None and seller_wallet.balance == 80
            assert len(transfers) == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
