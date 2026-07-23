import re
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from secure_coding_lab.models import (
    ChatMessage,
    ChatRoom,
    ChatRoomMember,
    ChatRoomType,
    Product,
    ProductStatus,
    User,
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
) -> UUID:
    async with database_factory() as database:
        user = User(username=username, password_hash=hash_password(PASSWORD))
        database.add(user)
        await database.commit()
        return user.id


async def create_product(
    database_factory: async_sessionmaker[AsyncSession],
    seller_id: UUID,
    *,
    status: ProductStatus = ProductStatus.ACTIVE,
) -> UUID:
    async with database_factory() as database:
        product = Product(
            seller_id=seller_id,
            name="안전한 카메라",
            description="상태가 좋은 중고 카메라입니다.",
            price=150000,
            image_key=f"{uuid4().hex}.png",
            status=status,
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


async def create_product_room(
    database_factory: async_sessionmaker[AsyncSession],
    seller_id: UUID,
    buyer_id: UUID,
    product_id: UUID,
) -> UUID:
    async with database_factory() as database:
        room = ChatRoom(
            type=ChatRoomType.PRODUCT,
            product_id=product_id,
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


@pytest.mark.asyncio
async def test_chat_pages_require_login(client: AsyncClient) -> None:
    response = await client.get("/chats")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_global_chat_is_shared_and_members_are_recorded(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_user(database_factory, "alice")
    await create_user(database_factory, "bob")

    await login(client, "alice")
    page = await client.get("/chats")
    first = await client.post(
        "/chats/global",
        data={"csrf_token": csrf_token(page.text)},
    )
    assert first.status_code == 303

    await login(client, "bob")
    page = await client.get("/chats")
    second = await client.post(
        "/chats/global",
        data={"csrf_token": csrf_token(page.text)},
    )
    assert second.status_code == 303
    assert second.headers["location"] == first.headers["location"]

    async with database_factory() as database:
        room_count = (
            await database.execute(
                select(func.count(ChatRoom.id)).where(ChatRoom.type == ChatRoomType.GLOBAL)
            )
        ).scalar_one()
        member_count = (
            await database.execute(select(func.count(ChatRoomMember.user_id)))
        ).scalar_one()
    assert room_count == 1
    assert member_count == 2


@pytest.mark.asyncio
async def test_global_chat_rejects_forged_csrf(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_user(database_factory, "alice")
    await login(client, "alice")

    response = await client.post(
        "/chats/global",
        data={"csrf_token": "forged"},
    )
    assert response.status_code == 403

    async with database_factory() as database:
        count = (await database.execute(select(func.count(ChatRoom.id)))).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_product_chat_is_reused_for_same_product_and_buyer(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = await create_user(database_factory, "seller")
    buyer_id = await create_user(database_factory, "buyer")
    product_id = await create_product(database_factory, seller_id)
    await login(client, "buyer")

    detail = await client.get(f"/products/{product_id}")
    first = await client.post(
        f"/products/{product_id}/chats",
        data={"csrf_token": csrf_token(detail.text)},
    )
    detail = await client.get(f"/products/{product_id}")
    second = await client.post(
        f"/products/{product_id}/chats",
        data={"csrf_token": csrf_token(detail.text)},
    )

    assert first.status_code == 303
    assert second.status_code == 303
    assert second.headers["location"] == first.headers["location"]
    async with database_factory() as database:
        rooms = (
            (
                await database.execute(
                    select(ChatRoom).where(
                        ChatRoom.product_id == product_id,
                        ChatRoom.buyer_id == buyer_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        members = (
            (
                await database.execute(
                    select(ChatRoomMember).where(ChatRoomMember.chat_room_id == rooms[0].id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rooms) == 1
    assert {member.user_id for member in members} == {seller_id, buyer_id}


@pytest.mark.asyncio
async def test_seller_cannot_create_chat_for_own_product(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = await create_user(database_factory, "seller")
    product_id = await create_product(database_factory, seller_id)
    await login(client, "seller")
    detail = await client.get(f"/products/{product_id}")

    response = await client.post(
        f"/products/{product_id}/chats",
        data={"csrf_token": csrf_token(detail.text)},
    )
    assert response.status_code == 400
    assert "자신의 상품" in response.text


@pytest.mark.asyncio
async def test_nonmember_cannot_view_or_send_to_product_chat(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = await create_user(database_factory, "seller")
    buyer_id = await create_user(database_factory, "buyer")
    await create_user(database_factory, "outsider")
    product_id = await create_product(database_factory, seller_id)
    room_id = await create_product_room(
        database_factory,
        seller_id,
        buyer_id,
        product_id,
    )
    await login(client, "outsider")

    view = await client.get(f"/chats/{room_id}")
    page = await client.get("/chats")
    send = await client.post(
        f"/chats/{room_id}/messages",
        data={"content": "권한 없는 메시지", "csrf_token": csrf_token(page.text)},
    )

    assert view.status_code == 404
    assert send.status_code == 404
    async with database_factory() as database:
        count = (await database.execute(select(func.count(ChatMessage.id)))).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_message_is_validated_escaped_and_updates_read_time(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(database_factory, "alice")
    await login(client, "alice")
    page = await client.get("/chats")
    joined = await client.post(
        "/chats/global",
        data={"csrf_token": csrf_token(page.text)},
    )
    room_url = joined.headers["location"]

    room_page = await client.get(room_url)
    empty = await client.post(
        f"{room_url}/messages",
        data={"content": "   ", "csrf_token": csrf_token(room_page.text)},
    )
    assert empty.status_code == 400
    assert "1자 이상 2000자 이하" in empty.text

    response = await client.post(
        f"{room_url}/messages",
        data={
            "content": "<script>alert(1)</script>",
            "csrf_token": csrf_token(empty.text),
        },
    )
    assert response.status_code == 303

    rendered = await client.get(room_url)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered.text
    assert "<script>alert(1)</script>" not in rendered.text
    async with database_factory() as database:
        message = (await database.execute(select(ChatMessage))).scalar_one()
        member = (
            await database.execute(select(ChatRoomMember).where(ChatRoomMember.user_id == user_id))
        ).scalar_one()
    assert message.content == "<script>alert(1)</script>"
    assert member.last_read_at is not None


@pytest.mark.asyncio
async def test_blocked_product_chat_does_not_accept_messages(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = await create_user(database_factory, "seller")
    buyer_id = await create_user(database_factory, "buyer")
    product_id = await create_product(
        database_factory,
        seller_id,
        status=ProductStatus.BLOCKED,
    )
    room_id = await create_product_room(
        database_factory,
        seller_id,
        buyer_id,
        product_id,
    )
    await login(client, "buyer")
    room_page = await client.get(f"/chats/{room_id}")

    response = await client.post(
        f"/chats/{room_id}/messages",
        data={"content": "전송 시도", "csrf_token": csrf_token(room_page.text)},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_database_rejects_duplicate_global_chat_rooms(
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with database_factory() as database:
        database.add_all(
            [
                ChatRoom(type=ChatRoomType.GLOBAL),
                ChatRoom(type=ChatRoomType.GLOBAL),
            ]
        )
        with pytest.raises(IntegrityError):
            await database.commit()


@pytest.mark.asyncio
async def test_database_rejects_duplicate_product_buyer_chat_rooms(
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller_id = await create_user(database_factory, "seller")
    buyer_id = await create_user(database_factory, "buyer")
    product_id = await create_product(database_factory, seller_id)
    async with database_factory() as database:
        database.add_all(
            [
                ChatRoom(
                    type=ChatRoomType.PRODUCT,
                    product_id=product_id,
                    buyer_id=buyer_id,
                ),
                ChatRoom(
                    type=ChatRoomType.PRODUCT,
                    product_id=product_id,
                    buyer_id=buyer_id,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await database.commit()
