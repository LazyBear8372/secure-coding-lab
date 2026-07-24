from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from secure_coding_lab.auth import get_optional_user
from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.db import get_db_session
from secure_coding_lab.models import (
    Product,
    ProductStatus,
    Report,
    ReportStatus,
    User,
    UserStatus,
)
from secure_coding_lab.security import normalize_username
from secure_coding_lab.web_security import csrf_is_valid, render_with_csrf

router = APIRouter(include_in_schema=False)

MIN_REASON_LENGTH = 10
MAX_REASON_LENGTH = 2000
STATUS_LABELS = {
    ReportStatus.PENDING: "검토 대기",
    ReportStatus.ACCEPTED: "승인",
    ReportStatus.REJECTED: "거절",
}


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


def validate_reason(reason: str) -> str:
    normalized = reason.strip()
    if not MIN_REASON_LENGTH <= len(normalized) <= MAX_REASON_LENGTH:
        raise ValueError(
            f"신고 사유는 {MIN_REASON_LENGTH}자 이상 {MAX_REASON_LENGTH}자 이하로 입력해 주세요."
        )
    return normalized


async def visible_user(database: AsyncSession, username: str) -> User | None:
    return await database.scalar(
        select(User).where(
            User.username == normalize_username(username),
            User.status != UserStatus.WITHDRAWN,
        )
    )


async def visible_product(database: AsyncSession, product_id: UUID) -> Product | None:
    return await database.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.status.in_([ProductStatus.ACTIVE, ProductStatus.SOLD]),
        )
    )


def report_form_response(
    request: Request,
    settings: Settings,
    user: User,
    *,
    target_user: User | None = None,
    target_product: Product | None = None,
    reason: str = "",
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return render_with_csrf(
        request,
        "report_form.html",
        settings=settings,
        context={
            "current_user": user,
            "target_user": target_user,
            "target_product": target_product,
            "reason": reason,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/users/{username}/report", response_class=HTMLResponse)
async def user_report_page(
    request: Request,
    username: str,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    target = await visible_user(database, username)
    if target is None or target.id == user.id:
        return HTMLResponse("신고 대상을 찾을 수 없습니다.", status_code=404)
    return report_form_response(request, settings, user, target_user=target)


@router.post("/users/{username}/reports", response_class=HTMLResponse)
async def report_user(
    request: Request,
    username: str,
    reason: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    target = await visible_user(database, username)
    if target is None or target.id == user.id:
        return HTMLResponse("신고 대상을 찾을 수 없습니다.", status_code=404)
    reporter_id = user.id
    target_id = target.id
    if not csrf_is_valid(request, csrf_token, settings):
        return report_form_response(
            request,
            settings,
            user,
            target_user=target,
            reason=reason,
            error="요청을 확인할 수 없습니다.",
            status_code=403,
        )
    try:
        normalized_reason = validate_reason(reason)
        database.add(
            Report(reporter_id=user.id, target_user_id=target.id, reason=normalized_reason)
        )
        await database.commit()
    except ValueError as error:
        return report_form_response(
            request,
            settings,
            user,
            target_user=target,
            reason=reason,
            error=str(error),
            status_code=400,
        )
    except IntegrityError:
        await database.rollback()
        refreshed_user = await database.get(User, reporter_id)
        target = await database.get(User, target_id)
        assert refreshed_user is not None and target is not None
        return report_form_response(
            request,
            settings,
            refreshed_user,
            target_user=target,
            reason=reason,
            error="이미 처리 중이거나 승인된 신고가 있습니다.",
            status_code=409,
        )
    return RedirectResponse("/me/reports?created=1", status_code=303)


@router.get("/products/{product_id}/report", response_class=HTMLResponse)
async def product_report_page(
    request: Request,
    product_id: UUID,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    target = await visible_product(database, product_id)
    if target is None or target.seller_id == user.id:
        return HTMLResponse("신고 대상을 찾을 수 없습니다.", status_code=404)
    return report_form_response(request, settings, user, target_product=target)


@router.post("/products/{product_id}/reports", response_class=HTMLResponse)
async def report_product(
    request: Request,
    product_id: UUID,
    reason: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    target = await visible_product(database, product_id)
    if target is None or target.seller_id == user.id:
        return HTMLResponse("신고 대상을 찾을 수 없습니다.", status_code=404)
    reporter_id = user.id
    target_id = target.id
    if not csrf_is_valid(request, csrf_token, settings):
        return report_form_response(
            request,
            settings,
            user,
            target_product=target,
            reason=reason,
            error="요청을 확인할 수 없습니다.",
            status_code=403,
        )
    try:
        normalized_reason = validate_reason(reason)
        database.add(
            Report(reporter_id=user.id, target_product_id=target.id, reason=normalized_reason)
        )
        await database.commit()
    except ValueError as error:
        return report_form_response(
            request,
            settings,
            user,
            target_product=target,
            reason=reason,
            error=str(error),
            status_code=400,
        )
    except IntegrityError:
        await database.rollback()
        refreshed_user = await database.get(User, reporter_id)
        target = await database.get(Product, target_id)
        assert refreshed_user is not None and target is not None
        return report_form_response(
            request,
            settings,
            refreshed_user,
            target_product=target,
            reason=reason,
            error="이미 처리 중이거나 승인된 신고가 있습니다.",
            status_code=409,
        )
    return RedirectResponse("/me/reports?created=1", status_code=303)


@router.get("/me/reports", response_class=HTMLResponse)
async def my_reports(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    reports = (
        await database.execute(
            select(Report, User, Product)
            .outerjoin(User, User.id == Report.target_user_id)
            .outerjoin(Product, Product.id == Report.target_product_id)
            .where(Report.reporter_id == user.id)
            .order_by(Report.created_at.desc(), Report.id.desc())
        )
    ).all()
    return render_with_csrf(
        request,
        "my_reports.html",
        settings=settings,
        context={
            "current_user": user,
            "reports": reports,
            "status_labels": STATUS_LABELS,
        },
    )
