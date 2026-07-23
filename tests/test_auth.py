import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from secure_coding_lab.models import Session, User, UserStatus
from secure_coding_lab.security import hash_password, hash_session_token

PASSWORD = "correct horse battery staple"


def csrf_token(response_text: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


async def signup(client: AsyncClient, username: str = "alice") -> None:
    page = await client.get("/signup")
    response = await client.post(
        "/signup",
        data={
            "username": username,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "csrf_token": csrf_token(page.text),
        },
    )
    assert response.status_code == 303


@pytest.mark.asyncio
async def test_signup_hashes_password(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup(client)

    async with database_factory() as database:
        user = (await database.execute(select(User))).scalar_one()

    assert user.username == "alice"
    assert user.password_hash != PASSWORD
    assert user.password_hash.startswith("$argon2id$")


@pytest.mark.asyncio
async def test_signup_rejects_duplicate_username(client: AsyncClient) -> None:
    await signup(client)
    page = await client.get("/signup")
    response = await client.post(
        "/signup",
        data={
            "username": "ALICE",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "csrf_token": csrf_token(page.text),
        },
    )

    assert response.status_code == 400
    assert "사용할 수 없는 아이디" in response.text


@pytest.mark.asyncio
async def test_signup_rejects_missing_csrf(client: AsyncClient) -> None:
    response = await client.post(
        "/signup",
        data={
            "username": "alice",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "csrf_token": "forged",
        },
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_login_stores_only_session_token_hash(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup(client)
    page = await client.get("/login")
    response = await client.post(
        "/login",
        data={
            "username": "alice",
            "password": PASSWORD,
            "csrf_token": csrf_token(page.text),
        },
    )

    assert response.status_code == 303
    session_token = response.cookies["session"]
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    async with database_factory() as database:
        session = (await database.execute(select(Session))).scalar_one()

    assert session.token_hash == hash_session_token(session_token)
    assert session.token_hash != session_token


@pytest.mark.asyncio
async def test_login_uses_generic_error_for_unknown_user(client: AsyncClient) -> None:
    page = await client.get("/login")
    response = await client.post(
        "/login",
        data={
            "username": "unknown",
            "password": PASSWORD,
            "csrf_token": csrf_token(page.text),
        },
    )

    assert response.status_code == 400
    assert "아이디 또는 비밀번호가 올바르지 않습니다." in response.text


@pytest.mark.asyncio
async def test_suspended_user_cannot_login(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with database_factory() as database:
        database.add(
            User(
                username="suspended",
                password_hash=hash_password(PASSWORD),
                status=UserStatus.SUSPENDED,
            )
        )
        await database.commit()

    page = await client.get("/login")
    response = await client.post(
        "/login",
        data={
            "username": "suspended",
            "password": PASSWORD,
            "csrf_token": csrf_token(page.text),
        },
    )

    assert response.status_code == 400
    assert "아이디 또는 비밀번호가 올바르지 않습니다." in response.text


@pytest.mark.asyncio
async def test_logout_revokes_session(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup(client)
    login_page = await client.get("/login")
    login_response = await client.post(
        "/login",
        data={
            "username": "alice",
            "password": PASSWORD,
            "csrf_token": csrf_token(login_page.text),
        },
    )
    client.cookies.update(login_response.cookies)
    logout_page = await client.get("/")
    assert "로그아웃" in logout_page.text

    response = await client.post(
        "/logout",
        data={"csrf_token": csrf_token(logout_page.text)},
    )

    assert response.status_code == 303
    async with database_factory() as database:
        session = (await database.execute(select(Session))).scalar_one()
    assert session.revoked_at is not None
