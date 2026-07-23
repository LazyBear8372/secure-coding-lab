from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from secure_coding_lab.db import get_db_session
from secure_coding_lab.models import Session, User, UserStatus
from secure_coding_lab.security import hash_session_token

SESSION_COOKIE_NAME = "session"
CSRF_COOKIE_NAME = "csrf_token"


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def get_optional_user(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_db_session)],
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    result = await database.execute(
        select(User, Session)
        .join(Session, Session.user_id == User.id)
        .where(
            Session.token_hash == hash_session_token(token),
            Session.revoked_at.is_(None),
            User.status == UserStatus.ACTIVE,
        )
    )
    row = result.one_or_none()
    if row is None:
        return None

    user, session = row
    if as_utc(session.expires_at) <= datetime.now(UTC):
        return None
    return user


async def require_user(
    user: Annotated[User | None, Depends(get_optional_user)],
) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")
    return user
