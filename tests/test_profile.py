import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from secure_coding_lab.models import Session, User, UserStatus

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "new correct horse battery staple"


def csrf_token(response_text: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


async def signup_and_login(client: AsyncClient, username: str = "alice") -> None:
    signup_page = await client.get("/signup")
    signup_response = await client.post(
        "/signup",
        data={
            "username": username,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "csrf_token": csrf_token(signup_page.text),
        },
    )
    assert signup_response.status_code == 303

    login_page = await client.get("/login")
    login_response = await client.post(
        "/login",
        data={
            "username": username,
            "password": PASSWORD,
            "csrf_token": csrf_token(login_page.text),
        },
    )
    assert login_response.status_code == 303


@pytest.mark.asyncio
async def test_my_page_requires_login(client: AsyncClient) -> None:
    response = await client.get("/me")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_user_can_update_bio_and_profile_escapes_html(client: AsyncClient) -> None:
    await signup_and_login(client)
    my_page = await client.get("/me")
    response = await client.post(
        "/me/bio",
        data={
            "bio": "<script>alert(1)</script>",
            "csrf_token": csrf_token(my_page.text),
        },
    )
    assert response.status_code == 303

    profile = await client.get("/users/alice")
    assert profile.status_code == 200
    assert "<script>" not in profile.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in profile.text


@pytest.mark.asyncio
async def test_bio_length_is_limited(client: AsyncClient) -> None:
    await signup_and_login(client)
    my_page = await client.get("/me")
    response = await client.post(
        "/me/bio",
        data={"bio": "a" * 1001, "csrf_token": csrf_token(my_page.text)},
    )

    assert response.status_code == 400
    assert "1000자 이하" in response.text


@pytest.mark.asyncio
async def test_password_change_revokes_all_sessions(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client)
    my_page = await client.get("/me")
    response = await client.post(
        "/me/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
            "csrf_token": csrf_token(my_page.text),
        },
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?password_changed=1"
    async with database_factory() as database:
        sessions = (await database.execute(select(Session))).scalars().all()
    assert all(session.revoked_at is not None for session in sessions)

    login_page = await client.get("/login")
    old_password_login = await client.post(
        "/login",
        data={
            "username": "alice",
            "password": PASSWORD,
            "csrf_token": csrf_token(login_page.text),
        },
    )
    assert old_password_login.status_code == 400

    login_page = await client.get("/login")
    new_password_login = await client.post(
        "/login",
        data={
            "username": "alice",
            "password": NEW_PASSWORD,
            "csrf_token": csrf_token(login_page.text),
        },
    )
    assert new_password_login.status_code == 303


@pytest.mark.asyncio
async def test_withdrawal_requires_password_and_revokes_sessions(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client)
    my_page = await client.get("/me")
    wrong_password = await client.post(
        "/me/withdraw",
        data={
            "current_password": "wrong-password",
            "csrf_token": csrf_token(my_page.text),
        },
    )
    assert wrong_password.status_code == 400

    my_page = await client.get("/me")
    response = await client.post(
        "/me/withdraw",
        data={
            "current_password": PASSWORD,
            "csrf_token": csrf_token(my_page.text),
        },
    )
    assert response.status_code == 303

    async with database_factory() as database:
        user = (await database.execute(select(User))).scalar_one()
        sessions = (await database.execute(select(Session))).scalars().all()
    assert user.status == UserStatus.WITHDRAWN
    assert user.withdrawn_at is not None
    assert all(session.revoked_at is not None for session in sessions)

    profile = await client.get("/users/alice")
    assert profile.status_code == 404
