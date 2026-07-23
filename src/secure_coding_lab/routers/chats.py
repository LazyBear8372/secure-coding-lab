from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from secure_coding_lab.auth import get_optional_user
from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.db import get_db_session
from secure_coding_lab.models import (
    ChatMessage,
    ChatRoom,
    ChatRoomMember,
    ChatRoomType,
    Product,
    ProductStatus,
    User,
    UserStatus,
)
from secure_coding_lab.web_security import csrf_is_valid, render_with_csrf

router = APIRouter(include_in_schema=False)

MAX_MESSAGE_LENGTH = 2000
MESSAGE_HISTORY_LIMIT = 100


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


def chat_not_found(
    request: Request,
    settings: Settings,
    user: User,
) -> HTMLResponse:
    return render_with_csrf(
        request,
        "chat_not_found.html",
        settings=settings,
        context={"current_user": user},
        status_code=status.HTTP_404_NOT_FOUND,
    )


async def room_for_member(
    database: AsyncSession,
    room_id: UUID,
    user_id: UUID,
) -> ChatRoom | None:
    result = await database.execute(
        select(ChatRoom)
        .join(ChatRoomMember, ChatRoomMember.chat_room_id == ChatRoom.id)
        .where(
            ChatRoom.id == room_id,
            ChatRoomMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def ensure_member(database: AsyncSession, room_id: UUID, user_id: UUID) -> None:
    member = await database.get(ChatRoomMember, (room_id, user_id))
    if member is not None:
        return
    try:
        async with database.begin_nested():
            database.add(ChatRoomMember(chat_room_id=room_id, user_id=user_id))
            await database.flush()
    except IntegrityError:
        member = await database.get(ChatRoomMember, (room_id, user_id))
        if member is None:
            raise


async def global_room(database: AsyncSession) -> ChatRoom:
    result = await database.execute(select(ChatRoom).where(ChatRoom.type == ChatRoomType.GLOBAL))
    room = result.scalar_one_or_none()
    if room is not None:
        return room

    try:
        async with database.begin_nested():
            room = ChatRoom(type=ChatRoomType.GLOBAL)
            database.add(room)
            await database.flush()
            return room
    except IntegrityError:
        result = await database.execute(
            select(ChatRoom).where(ChatRoom.type == ChatRoomType.GLOBAL)
        )
        return result.scalar_one()


async def product_room(
    database: AsyncSession,
    product: Product,
    buyer: User,
) -> ChatRoom:
    result = await database.execute(
        select(ChatRoom).where(
            ChatRoom.type == ChatRoomType.PRODUCT,
            ChatRoom.product_id == product.id,
            ChatRoom.buyer_id == buyer.id,
        )
    )
    room = result.scalar_one_or_none()
    if room is None:
        try:
            async with database.begin_nested():
                room = ChatRoom(
                    type=ChatRoomType.PRODUCT,
                    product_id=product.id,
                    buyer_id=buyer.id,
                )
                database.add(room)
                await database.flush()
        except IntegrityError:
            result = await database.execute(
                select(ChatRoom).where(
                    ChatRoom.type == ChatRoomType.PRODUCT,
                    ChatRoom.product_id == product.id,
                    ChatRoom.buyer_id == buyer.id,
                )
            )
            room = result.scalar_one()

    await ensure_member(database, room.id, product.seller_id)
    await ensure_member(database, room.id, buyer.id)
    return room


async def chat_context(
    database: AsyncSession,
    room: ChatRoom,
    user: User,
    *,
    error: str | None = None,
) -> dict[str, object]:
    product = await database.get(Product, room.product_id) if room.product_id else None
    seller = await database.get(User, product.seller_id) if product else None
    buyer = await database.get(User, room.buyer_id) if room.buyer_id else None
    result = await database.execute(
        select(ChatMessage, User)
        .join(User, User.id == ChatMessage.sender_id)
        .where(ChatMessage.chat_room_id == room.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(MESSAGE_HISTORY_LIMIT)
    )
    messages = list(reversed(result.all()))
    can_send = product is None or product.status in (ProductStatus.ACTIVE, ProductStatus.SOLD)
    return {
        "current_user": user,
        "room": room,
        "product": product,
        "seller": seller,
        "buyer": buyer,
        "messages": messages,
        "can_send": can_send,
        "error": error,
    }


async def render_room(
    request: Request,
    settings: Settings,
    database: AsyncSession,
    room: ChatRoom,
    user: User,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    context = await chat_context(database, room, user, error=error)
    member = await database.get(ChatRoomMember, (room.id, user.id))
    if member is not None:
        member.last_read_at = datetime.now(UTC)
        await database.commit()
    return render_with_csrf(
        request,
        "chat_room.html",
        settings=settings,
        context=context,
        status_code=status_code,
    )


@router.get("/chats", response_class=HTMLResponse)
async def chat_list(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()

    seller = aliased(User)
    buyer = aliased(User)
    result = await database.execute(
        select(ChatRoom, Product, seller, buyer)
        .join(ChatRoomMember, ChatRoomMember.chat_room_id == ChatRoom.id)
        .outerjoin(Product, Product.id == ChatRoom.product_id)
        .outerjoin(seller, seller.id == Product.seller_id)
        .outerjoin(buyer, buyer.id == ChatRoom.buyer_id)
        .where(ChatRoomMember.user_id == user.id)
        .order_by(ChatRoom.created_at.desc(), ChatRoom.id.desc())
    )
    return render_with_csrf(
        request,
        "chat_list.html",
        settings=settings,
        context={"current_user": user, "rooms": result.all()},
    )


@router.post("/chats/global")
async def join_global_chat(
    request: Request,
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    if not csrf_is_valid(request, csrf_token, settings):
        return HTMLResponse("요청을 확인할 수 없습니다.", status_code=status.HTTP_403_FORBIDDEN)

    room = await global_room(database)
    await ensure_member(database, room.id, user.id)
    await database.commit()
    return RedirectResponse(f"/chats/{room.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/products/{product_id}/chats")
async def create_product_chat(
    request: Request,
    product_id: UUID,
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    if not csrf_is_valid(request, csrf_token, settings):
        return HTMLResponse("요청을 확인할 수 없습니다.", status_code=status.HTTP_403_FORBIDDEN)

    result = await database.execute(
        select(Product)
        .join(User, User.id == Product.seller_id)
        .where(
            Product.id == product_id,
            Product.status == ProductStatus.ACTIVE,
            User.status == UserStatus.ACTIVE,
        )
    )
    product = result.scalar_one_or_none()
    if product is None:
        return chat_not_found(request, settings, user)
    if product.seller_id == user.id:
        return HTMLResponse(
            "자신의 상품에는 구매 채팅을 만들 수 없습니다.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    room = await product_room(database, product, user)
    await database.commit()
    return RedirectResponse(f"/chats/{room.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/chats/{room_id}", response_class=HTMLResponse)
async def chat_room(
    request: Request,
    room_id: UUID,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    room = await room_for_member(database, room_id, user.id)
    if room is None:
        return chat_not_found(request, settings, user)
    return await render_room(request, settings, database, room, user)


@router.post("/chats/{room_id}/messages", response_class=HTMLResponse)
async def send_message(
    request: Request,
    room_id: UUID,
    content: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    if not csrf_is_valid(request, csrf_token, settings):
        return HTMLResponse("요청을 확인할 수 없습니다.", status_code=status.HTTP_403_FORBIDDEN)

    room = await room_for_member(database, room_id, user.id)
    if room is None:
        return chat_not_found(request, settings, user)
    if room.product_id is not None:
        product = await database.get(Product, room.product_id)
        if product is None or product.status not in (ProductStatus.ACTIVE, ProductStatus.SOLD):
            return chat_not_found(request, settings, user)

    normalized_content = content.strip()
    if not normalized_content or len(normalized_content) > MAX_MESSAGE_LENGTH:
        return await render_room(
            request,
            settings,
            database,
            room,
            user,
            error=f"메시지는 1자 이상 {MAX_MESSAGE_LENGTH}자 이하로 입력해 주세요.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    database.add(
        ChatMessage(
            chat_room_id=room.id,
            sender_id=user.id,
            content=normalized_content,
        )
    )
    await database.commit()
    return RedirectResponse(
        f"/chats/{room.id}#messages",
        status_code=status.HTTP_303_SEE_OTHER,
    )
