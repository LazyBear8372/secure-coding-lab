from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from secure_coding_lab.auth import get_optional_user
from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.db import get_db_session
from secure_coding_lab.models import User, Wallet, WalletTransfer, WalletTransferType
from secure_coding_lab.web_security import csrf_is_valid, render_with_csrf

router = APIRouter(include_in_schema=False)

MAX_AMOUNT = 9_223_372_036_854_775_807
PAGE_SIZE = 20
MAX_PAGE = 10_000
TRANSFER_LABELS = {
    WalletTransferType.DEPOSIT: "입금",
    WalletTransferType.WITHDRAWAL: "출금",
    WalletTransferType.TRANSFER: "송금",
}


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


def parse_amount(raw_amount: str) -> int:
    try:
        amount = int(raw_amount.strip())
    except ValueError:
        raise ValueError("금액은 1 이상의 정수로 입력해 주세요.") from None
    if not 0 < amount <= MAX_AMOUNT:
        raise ValueError("금액은 1 이상의 정수로 입력해 주세요.")
    return amount


def parse_idempotency_key(raw_key: str) -> UUID:
    try:
        return UUID(raw_key)
    except ValueError:
        raise ValueError("요청을 확인할 수 없습니다. 다시 시도해 주세요.") from None


async def wallet_for_user(database: AsyncSession, user: User) -> Wallet:
    wallet = await database.scalar(select(Wallet).where(Wallet.user_id == user.id))
    if wallet is not None:
        return wallet

    wallet = Wallet(user_id=user.id)
    database.add(wallet)
    try:
        await database.flush()
    except IntegrityError:
        await database.rollback()
        wallet = await database.scalar(select(Wallet).where(Wallet.user_id == user.id))
        assert wallet is not None
    return wallet


async def transfer_for_key(
    database: AsyncSession, wallet: Wallet, key: UUID
) -> WalletTransfer | None:
    return await database.scalar(
        select(WalletTransfer).where(
            WalletTransfer.idempotency_key == key,
            or_(
                WalletTransfer.sender_wallet_id == wallet.id,
                WalletTransfer.receiver_wallet_id == wallet.id,
            ),
        )
    )


async def wallet_context(database: AsyncSession, user: User, page: int) -> dict[str, object]:
    wallet = await wallet_for_user(database, user)
    await database.flush()
    transfers = (
        (
            await database.execute(
                select(WalletTransfer)
                .where(
                    or_(
                        WalletTransfer.sender_wallet_id == wallet.id,
                        WalletTransfer.receiver_wallet_id == wallet.id,
                    )
                )
                .order_by(WalletTransfer.created_at.desc(), WalletTransfer.id.desc())
                .limit(PAGE_SIZE + 1)
                .offset((page - 1) * PAGE_SIZE)
            )
        )
        .scalars()
        .all()
    )
    return {
        "current_user": user,
        "wallet": wallet,
        "transfers": transfers[:PAGE_SIZE],
        "transfer_labels": TRANSFER_LABELS,
        "page": page,
        "has_next": len(transfers) > PAGE_SIZE,
        "deposit_key": str(uuid4()),
        "withdrawal_key": str(uuid4()),
    }


async def wallet_page_response(
    request: Request,
    database: AsyncSession,
    user: User,
    settings: Settings,
    *,
    page: int = 1,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    context = await wallet_context(database, user, page)
    if error:
        context["error"] = error
    return render_with_csrf(
        request, "wallet.html", settings=settings, context=context, status_code=status_code
    )


@router.get("/wallet", response_class=HTMLResponse)
async def wallet_page(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    page: int = 1,
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    if not 1 <= page <= MAX_PAGE:
        return await wallet_page_response(
            request, database, user, settings, error="올바르지 않은 페이지입니다.", status_code=400
        )
    return await wallet_page_response(request, database, user, settings, page=page)


async def change_balance(
    request: Request,
    amount: str,
    idempotency_key: str,
    csrf_token: str,
    user: User | None,
    database: AsyncSession,
    settings: Settings,
    transfer_type: WalletTransferType,
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    if not csrf_is_valid(request, csrf_token, settings):
        return await wallet_page_response(
            request, database, user, settings, error="요청을 확인할 수 없습니다.", status_code=403
        )
    try:
        parsed_amount = parse_amount(amount)
        key = parse_idempotency_key(idempotency_key)
    except ValueError as error:
        return await wallet_page_response(
            request, database, user, settings, error=str(error), status_code=400
        )

    wallet = await wallet_for_user(database, user)
    if await transfer_for_key(database, wallet, key):
        return RedirectResponse("/wallet?updated=1", status_code=status.HTTP_303_SEE_OTHER)

    try:
        if transfer_type == WalletTransferType.WITHDRAWAL:
            result = await database.execute(
                update(Wallet)
                .where(Wallet.id == wallet.id, Wallet.balance >= parsed_amount)
                .values(balance=Wallet.balance - parsed_amount)
            )
            if result.rowcount != 1:
                await database.commit()
                return await wallet_page_response(
                    request, database, user, settings, error="잔액이 부족합니다.", status_code=400
                )
            transfer = WalletTransfer(
                sender_wallet_id=wallet.id,
                amount=parsed_amount,
                type=transfer_type,
                idempotency_key=key,
            )
        else:
            await database.execute(
                update(Wallet)
                .where(Wallet.id == wallet.id)
                .values(balance=Wallet.balance + parsed_amount)
            )
            transfer = WalletTransfer(
                receiver_wallet_id=wallet.id,
                amount=parsed_amount,
                type=transfer_type,
                idempotency_key=key,
            )
        database.add(transfer)
        await database.commit()
    except IntegrityError:
        await database.rollback()
        if await transfer_for_key(database, wallet, key):
            return RedirectResponse("/wallet?updated=1", status_code=status.HTTP_303_SEE_OTHER)
        return await wallet_page_response(
            request, database, user, settings, error="요청을 처리하지 못했습니다.", status_code=409
        )
    return RedirectResponse("/wallet?updated=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/wallet/deposit", response_class=HTMLResponse)
async def deposit(
    request: Request,
    amount: Annotated[str, Form()],
    idempotency_key: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    return await change_balance(
        request,
        amount,
        idempotency_key,
        csrf_token,
        user,
        database,
        settings,
        WalletTransferType.DEPOSIT,
    )


@router.post("/wallet/withdraw", response_class=HTMLResponse)
async def withdraw(
    request: Request,
    amount: Annotated[str, Form()],
    idempotency_key: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    return await change_balance(
        request,
        amount,
        idempotency_key,
        csrf_token,
        user,
        database,
        settings,
        WalletTransferType.WITHDRAWAL,
    )
