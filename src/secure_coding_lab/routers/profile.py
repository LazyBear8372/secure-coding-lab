from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from secure_coding_lab.auth import SESSION_COOKIE_NAME, get_optional_user
from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.db import get_db_session
from secure_coding_lab.models import Session, User, UserStatus
from secure_coding_lab.security import (
    hash_password,
    is_valid_password,
    normalize_username,
    verify_password,
)
from secure_coding_lab.templating import templates
from secure_coding_lab.web_security import csrf_is_valid, render_with_csrf

router = APIRouter(include_in_schema=False)

MAX_BIO_LENGTH = 1000


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


def clear_session_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/users/{username}", response_class=HTMLResponse)
async def public_profile(
    request: Request,
    username: str,
    database: Annotated[AsyncSession, Depends(get_db_session)],
) -> HTMLResponse:
    normalized_username = normalize_username(username)
    result = await database.execute(
        select(User).where(
            User.username == normalized_username,
            User.status != UserStatus.WITHDRAWN,
        )
    )
    profile_user = result.scalar_one_or_none()
    if profile_user is None:
        return templates.TemplateResponse(
            request=request,
            name="profile_not_found.html",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={"profile_user": profile_user},
    )


@router.get("/me", response_class=HTMLResponse)
async def my_page(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    updated: str | None = None,
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    notice = {
        "bio": "소개글이 변경되었습니다.",
    }.get(updated)
    return render_with_csrf(
        request,
        "my_page.html",
        settings=settings,
        context={"current_user": user, "notice": notice},
    )


@router.post("/me/bio", response_class=HTMLResponse)
async def update_bio(
    request: Request,
    bio: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    if not csrf_is_valid(request, csrf_token, settings):
        return render_with_csrf(
            request,
            "my_page.html",
            settings=settings,
            context={"current_user": user, "error": "요청을 확인할 수 없습니다."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    normalized_bio = bio.strip()
    if len(normalized_bio) > MAX_BIO_LENGTH:
        return render_with_csrf(
            request,
            "my_page.html",
            settings=settings,
            context={
                "current_user": user,
                "error": f"소개글은 {MAX_BIO_LENGTH}자 이하로 입력해 주세요.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.bio = normalized_bio
    user.updated_at = datetime.now(UTC)
    await database.commit()
    return RedirectResponse("/me?updated=bio", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/me/password", response_class=HTMLResponse)
async def update_password(
    request: Request,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    new_password_confirm: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    error_context = {"current_user": user}
    if not csrf_is_valid(request, csrf_token, settings):
        return render_with_csrf(
            request,
            "my_page.html",
            settings=settings,
            context={**error_context, "error": "요청을 확인할 수 없습니다."},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not verify_password(user.password_hash, current_password):
        return render_with_csrf(
            request,
            "my_page.html",
            settings=settings,
            context={**error_context, "error": "현재 비밀번호가 올바르지 않습니다."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not is_valid_password(new_password):
        return render_with_csrf(
            request,
            "my_page.html",
            settings=settings,
            context={
                **error_context,
                "error": "새 비밀번호는 12자 이상 128자 이하로 입력해 주세요.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if new_password != new_password_confirm:
        return render_with_csrf(
            request,
            "my_page.html",
            settings=settings,
            context={**error_context, "error": "새 비밀번호 확인이 일치하지 않습니다."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if verify_password(user.password_hash, new_password):
        return render_with_csrf(
            request,
            "my_page.html",
            settings=settings,
            context={**error_context, "error": "기존 비밀번호와 다른 비밀번호를 사용해 주세요."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    now = datetime.now(UTC)
    user.password_hash = hash_password(new_password)
    user.updated_at = now
    await database.execute(
        update(Session)
        .where(Session.user_id == user.id, Session.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await database.commit()

    response = RedirectResponse(
        "/login?password_changed=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    clear_session_cookie(response)
    return response


@router.post("/me/withdraw", response_class=HTMLResponse)
async def withdraw(
    request: Request,
    current_password: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    if not csrf_is_valid(request, csrf_token, settings):
        return render_with_csrf(
            request,
            "my_page.html",
            settings=settings,
            context={"current_user": user, "error": "요청을 확인할 수 없습니다."},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not verify_password(user.password_hash, current_password):
        return render_with_csrf(
            request,
            "my_page.html",
            settings=settings,
            context={"current_user": user, "error": "현재 비밀번호가 올바르지 않습니다."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    now = datetime.now(UTC)
    user.status = UserStatus.WITHDRAWN
    user.withdrawn_at = now
    user.updated_at = now
    await database.execute(
        update(Session)
        .where(Session.user_id == user.id, Session.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await database.commit()

    response = RedirectResponse("/?withdrawn=1", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response
