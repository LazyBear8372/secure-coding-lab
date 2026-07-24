from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from secure_coding_lab.auth import get_optional_user
from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.db import get_db_session
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
from secure_coding_lab.moderation import apply_automatic_block
from secure_coding_lab.product_images import delete_product_image
from secure_coding_lab.web_security import csrf_is_valid, render_with_csrf

router = APIRouter(prefix="/admin", include_in_schema=False)

MIN_REASON_LENGTH = 5
MAX_REASON_LENGTH = 1000


def admin_denied(user: User | None) -> HTMLResponse | RedirectResponse | None:
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.role != UserRole.ADMIN:
        return HTMLResponse("관리자 권한이 필요합니다.", status_code=403)
    return None


def validate_reason(reason: str) -> str:
    normalized = reason.strip()
    if not MIN_REASON_LENGTH <= len(normalized) <= MAX_REASON_LENGTH:
        raise ValueError(
            f"처리 사유는 {MIN_REASON_LENGTH}자 이상 {MAX_REASON_LENGTH}자 이하로 입력해 주세요."
        )
    return normalized


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if denied := admin_denied(user):
        return denied
    assert user is not None
    pending_reports = await database.scalar(
        select(func.count(Report.id)).where(Report.status == ReportStatus.PENDING)
    )
    suspended_users = await database.scalar(
        select(func.count(User.id)).where(User.status == UserStatus.SUSPENDED)
    )
    blocked_products = await database.scalar(
        select(func.count(Product.id)).where(Product.status == ProductStatus.BLOCKED)
    )
    return render_with_csrf(
        request,
        "admin_dashboard.html",
        settings=settings,
        context={
            "current_user": user,
            "pending_reports": pending_reports,
            "suspended_users": suspended_users,
            "blocked_products": blocked_products,
        },
    )


@router.get("/reports", response_class=HTMLResponse)
async def report_list(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if denied := admin_denied(user):
        return denied
    assert user is not None
    reporter = aliased(User)
    target_user = aliased(User)
    reviewer = aliased(User)
    reports = (
        await database.execute(
            select(Report, reporter, target_user, Product, reviewer)
            .join(reporter, reporter.id == Report.reporter_id)
            .outerjoin(target_user, target_user.id == Report.target_user_id)
            .outerjoin(Product, Product.id == Report.target_product_id)
            .outerjoin(reviewer, reviewer.id == Report.reviewed_by_id)
            .order_by(Report.status.asc(), Report.created_at.asc())
        )
    ).all()
    return render_with_csrf(
        request,
        "admin_reports.html",
        settings=settings,
        context={"current_user": user, "reports": reports},
    )


@router.post("/reports/{report_id}/review", response_class=HTMLResponse)
async def review_report(
    request: Request,
    report_id: UUID,
    decision: Annotated[str, Form()],
    reason: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if denied := admin_denied(user):
        return denied
    assert user is not None
    if not csrf_is_valid(request, csrf_token, settings):
        return HTMLResponse("요청을 확인할 수 없습니다.", status_code=403)
    if decision not in ("accepted", "rejected"):
        return HTMLResponse("올바르지 않은 심사 결과입니다.", status_code=400)
    try:
        normalized_reason = validate_reason(reason)
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)

    report = await database.scalar(select(Report).where(Report.id == report_id).with_for_update())
    if report is None:
        return HTMLResponse("신고를 찾을 수 없습니다.", status_code=404)
    if report.status != ReportStatus.PENDING:
        return HTMLResponse("이미 심사된 신고입니다.", status_code=409)

    report.status = ReportStatus.ACCEPTED if decision == "accepted" else ReportStatus.REJECTED
    report.reviewed_at = datetime.now(UTC)
    report.reviewed_by_id = user.id
    report.review_reason = normalized_reason
    database.add(
        AdminAuditLog(
            admin_id=user.id,
            action=f"report.{decision}",
            report_id=report.id,
            reason=normalized_reason,
        )
    )
    if report.status == ReportStatus.ACCEPTED:
        blocked = await apply_automatic_block(database, report, settings)
        if blocked:
            database.add(
                AdminAuditLog(
                    admin_id=user.id,
                    action=(
                        "automatic.user_suspended"
                        if report.target_user_id is not None
                        else "automatic.product_blocked"
                    ),
                    target_user_id=report.target_user_id,
                    target_product_id=report.target_product_id,
                    reason="승인 신고 누적 기준 도달",
                )
            )
    await database.commit()
    return RedirectResponse("/admin/reports?reviewed=1", status_code=303)


@router.get("/users", response_class=HTMLResponse)
async def user_list(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if denied := admin_denied(user):
        return denied
    assert user is not None
    users = (
        (await database.execute(select(User).order_by(User.created_at.desc(), User.id.desc())))
        .scalars()
        .all()
    )
    return render_with_csrf(
        request,
        "admin_users.html",
        settings=settings,
        context={"current_user": user, "users": users},
    )


@router.post("/users/{user_id}/status", response_class=HTMLResponse)
async def update_user_status(
    request: Request,
    user_id: UUID,
    action: Annotated[str, Form()],
    reason: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if denied := admin_denied(user):
        return denied
    assert user is not None
    if not csrf_is_valid(request, csrf_token, settings):
        return HTMLResponse("요청을 확인할 수 없습니다.", status_code=403)
    if action not in ("suspend", "restore"):
        return HTMLResponse("올바르지 않은 상태 변경입니다.", status_code=400)
    try:
        normalized_reason = validate_reason(reason)
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    if user_id == user.id:
        return HTMLResponse("자신의 관리자 상태는 변경할 수 없습니다.", status_code=400)

    target = await database.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None or target.status == UserStatus.WITHDRAWN:
        return HTMLResponse("사용자를 찾을 수 없습니다.", status_code=404)
    now = datetime.now(UTC)
    if action == "suspend" and target.status == UserStatus.ACTIVE:
        target.status = UserStatus.SUSPENDED
        target.suspended_at = now
        await database.execute(
            update(Session)
            .where(Session.user_id == target.id, Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    elif action == "restore" and target.status == UserStatus.SUSPENDED:
        target.status = UserStatus.ACTIVE
        target.suspended_at = None
    else:
        return HTMLResponse("이미 요청한 상태입니다.", status_code=409)
    target.updated_at = now
    database.add(
        AdminAuditLog(
            admin_id=user.id,
            action=f"user.{action}",
            target_user_id=target.id,
            reason=normalized_reason,
        )
    )
    await database.commit()
    return RedirectResponse("/admin/users?updated=1", status_code=303)


@router.get("/products", response_class=HTMLResponse)
async def product_list(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if denied := admin_denied(user):
        return denied
    assert user is not None
    products = (
        await database.execute(
            select(Product, User)
            .join(User, User.id == Product.seller_id)
            .where(Product.status != ProductStatus.DELETED)
            .order_by(Product.created_at.desc(), Product.id.desc())
        )
    ).all()
    return render_with_csrf(
        request,
        "admin_products.html",
        settings=settings,
        context={"current_user": user, "products": products},
    )


@router.post("/products/{product_id}/delete", response_class=HTMLResponse)
async def delete_product(
    request: Request,
    product_id: UUID,
    reason: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if denied := admin_denied(user):
        return denied
    assert user is not None
    if not csrf_is_valid(request, csrf_token, settings):
        return HTMLResponse("요청을 확인할 수 없습니다.", status_code=403)
    try:
        normalized_reason = validate_reason(reason)
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    product = await database.scalar(
        select(Product)
        .where(Product.id == product_id, Product.status != ProductStatus.DELETED)
        .with_for_update()
    )
    if product is None:
        return HTMLResponse("상품을 찾을 수 없습니다.", status_code=404)
    now = datetime.now(UTC)
    product.status = ProductStatus.DELETED
    product.deleted_at = now
    product.updated_at = now
    image_key = product.image_key
    database.add(
        AdminAuditLog(
            admin_id=user.id,
            action="product.delete",
            target_product_id=product.id,
            reason=normalized_reason,
        )
    )
    await database.commit()
    delete_product_image(Path(settings.upload_dir), image_key)
    return RedirectResponse("/admin/products?deleted=1", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
async def audit_list(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if denied := admin_denied(user):
        return denied
    assert user is not None
    admin_user = aliased(User)
    logs = (
        await database.execute(
            select(AdminAuditLog, admin_user)
            .join(admin_user, admin_user.id == AdminAuditLog.admin_id)
            .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
            .limit(200)
        )
    ).all()
    return render_with_csrf(
        request,
        "admin_audit.html",
        settings=settings,
        context={"current_user": user, "logs": logs},
    )
