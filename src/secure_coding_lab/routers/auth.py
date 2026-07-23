from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from secure_coding_lab.auth import SESSION_COOKIE_NAME
from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.db import get_db_session
from secure_coding_lab.models import Session, User, UserStatus
from secure_coding_lab.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    hash_session_token,
    is_valid_password,
    is_valid_username,
    make_session_token,
    normalize_username,
    verify_password,
)
from secure_coding_lab.web_security import cookie_is_secure, csrf_is_valid, render_with_csrf

router = APIRouter(include_in_schema=False)


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    return render_with_csrf(request, "signup.html", settings=settings)


@router.post("/signup", response_class=HTMLResponse)
async def signup(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_confirm: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    normalized_username = normalize_username(username)
    form_context = {"username": normalized_username}

    if not csrf_is_valid(request, csrf_token, settings):
        return render_with_csrf(
            request,
            "signup.html",
            settings=settings,
            context={**form_context, "error": "요청을 확인할 수 없습니다. 다시 시도해 주세요."},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not is_valid_username(normalized_username):
        return render_with_csrf(
            request,
            "signup.html",
            settings=settings,
            context={
                **form_context,
                "error": "아이디는 영문 소문자, 숫자, 밑줄 3~32자로 입력해 주세요.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not is_valid_password(password):
        return render_with_csrf(
            request,
            "signup.html",
            settings=settings,
            context={
                **form_context,
                "error": "비밀번호는 12자 이상 128자 이하로 입력해 주세요.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if password != password_confirm:
        return render_with_csrf(
            request,
            "signup.html",
            settings=settings,
            context={**form_context, "error": "비밀번호 확인이 일치하지 않습니다."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    database.add(User(username=normalized_username, password_hash=hash_password(password)))
    try:
        await database.commit()
    except IntegrityError:
        await database.rollback()
        return render_with_csrf(
            request,
            "signup.html",
            settings=settings,
            context={**form_context, "error": "사용할 수 없는 아이디입니다."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return RedirectResponse("/login?registered=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    registered: bool = False,
    password_changed: bool = False,
) -> HTMLResponse:
    notice = None
    if registered:
        notice = "회원가입이 완료되었습니다."
    elif password_changed:
        notice = "비밀번호가 변경되었습니다. 다시 로그인해 주세요."
    context = {"notice": notice} if notice else None
    return render_with_csrf(request, "login.html", settings=settings, context=context)


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    normalized_username = normalize_username(username)
    error_context = {
        "username": normalized_username,
        "error": "아이디 또는 비밀번호가 올바르지 않습니다.",
    }
    if not csrf_is_valid(request, csrf_token, settings):
        return render_with_csrf(
            request,
            "login.html",
            settings=settings,
            context={"username": normalized_username, "error": "요청을 확인할 수 없습니다."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    result = await database.execute(select(User).where(User.username == normalized_username))
    user = result.scalar_one_or_none()
    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_matches = verify_password(password_hash, password)
    if user is None or user.status != UserStatus.ACTIVE or not password_matches:
        return render_with_csrf(
            request,
            "login.html",
            settings=settings,
            context=error_context,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    old_token = request.cookies.get(SESSION_COOKIE_NAME)
    if old_token:
        await database.execute(
            update(Session)
            .where(
                Session.token_hash == hash_session_token(old_token),
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )

    session_token = make_session_token()
    database.add(
        Session(
            user_id=user.id,
            token_hash=hash_session_token(session_token),
            expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
        )
    )
    await database.commit()

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=settings.session_ttl_hours * 60 * 60,
        httponly=True,
        secure=cookie_is_secure(settings),
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    csrf_token: Annotated[str, Form()],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if not csrf_is_valid(request, csrf_token, settings):
        return HTMLResponse("요청을 확인할 수 없습니다.", status_code=status.HTTP_403_FORBIDDEN)

    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        await database.execute(
            update(Session)
            .where(
                Session.token_hash == hash_session_token(session_token),
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await database.commit()

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response
