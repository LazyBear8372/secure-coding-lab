import io
import re
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.main import app
from secure_coding_lab.models import Product, ProductStatus
from secure_coding_lab.product_images import MAX_IMAGE_BYTES

PASSWORD = "correct horse battery staple"


def csrf_token(response_text: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def image_bytes(image_format: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color=(38, 115, 74)).save(buffer, format=image_format)
    return buffer.getvalue()


async def signup_and_login(client: AsyncClient, username: str) -> None:
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
    client.cookies.update(login_response.cookies)


async def create_product(client: AsyncClient, *, name: str = "안전한 카메라") -> None:
    page = await client.get("/products/new")
    response = await client.post(
        "/products",
        data={
            "name": name,
            "description": "상태가 좋은 중고 카메라입니다.",
            "price": "150000",
            "csrf_token": csrf_token(page.text),
        },
        files={"image": ("user-controlled.png", image_bytes(), "image/png")},
    )
    assert response.status_code == 303


def upload_dir() -> Path:
    settings = app.dependency_overrides[get_settings]()
    assert isinstance(settings, Settings)
    return Path(settings.upload_dir)


@pytest.mark.asyncio
async def test_product_creation_requires_login(client: AsyncClient) -> None:
    response = await client.get("/products/new")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_product_creation_rejects_forged_csrf(client: AsyncClient) -> None:
    await signup_and_login(client, "seller")
    response = await client.post(
        "/products",
        data={
            "name": "카메라",
            "description": "설명",
            "price": "1000",
            "csrf_token": "forged",
        },
        files={"image": ("camera.png", image_bytes(), "image/png")},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_product_creation_sanitizes_image_and_stores_random_key(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client, "seller")
    await create_product(client)

    async with database_factory() as database:
        product = (await database.execute(select(Product))).scalar_one()

    assert product.name == "안전한 카메라"
    assert product.price == 150000
    assert product.image_key != "user-controlled.png"
    assert re.fullmatch(r"[0-9a-f]{32}\.png", product.image_key)
    stored_path = upload_dir() / product.image_key
    assert stored_path.is_file()
    with Image.open(stored_path) as stored:
        assert stored.format == "PNG"
        assert stored.size == (16, 16)

    response = await client.get(f"/product-images/{product.image_key}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


@pytest.mark.asyncio
@pytest.mark.parametrize("price", ["-1", "1.5", "9223372036854775808"])
async def test_product_creation_rejects_invalid_price(client: AsyncClient, price: str) -> None:
    await signup_and_login(client, f"seller{price.replace('.', 'x').replace('-', 'n')}")
    page = await client.get("/products/new")
    response = await client.post(
        "/products",
        data={
            "name": "카메라",
            "description": "설명",
            "price": price,
            "csrf_token": csrf_token(page.text),
        },
        files={"image": ("camera.png", image_bytes(), "image/png")},
    )
    assert response.status_code == 400
    assert "가격은 0 이상의 정수" in response.text


@pytest.mark.asyncio
async def test_product_creation_rejects_mime_content_mismatch(client: AsyncClient) -> None:
    await signup_and_login(client, "seller")
    page = await client.get("/products/new")
    response = await client.post(
        "/products",
        data={
            "name": "카메라",
            "description": "설명",
            "price": "1000",
            "csrf_token": csrf_token(page.text),
        },
        files={"image": ("fake.jpg", image_bytes("PNG"), "image/jpeg")},
    )
    assert response.status_code == 400
    assert "실제 이미지 내용" in response.text


@pytest.mark.asyncio
async def test_product_creation_rejects_oversized_image(client: AsyncClient) -> None:
    await signup_and_login(client, "seller")
    page = await client.get("/products/new")
    response = await client.post(
        "/products",
        data={
            "name": "카메라",
            "description": "설명",
            "price": "1000",
            "csrf_token": csrf_token(page.text),
        },
        files={"image": ("large.png", b"x" * (MAX_IMAGE_BYTES + 1), "image/png")},
    )
    assert response.status_code == 400
    assert "5MB 이하" in response.text


@pytest.mark.asyncio
async def test_product_creation_rejects_overlong_name(client: AsyncClient) -> None:
    await signup_and_login(client, "seller")
    page = await client.get("/products/new")
    response = await client.post(
        "/products",
        data={
            "name": "가" * 121,
            "description": "설명",
            "price": "1000",
            "csrf_token": csrf_token(page.text),
        },
        files={"image": ("camera.png", image_bytes(), "image/png")},
    )
    assert response.status_code == 400
    assert "120자 이하" in response.text


@pytest.mark.asyncio
async def test_product_detail_escapes_stored_html(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client, "seller")
    await create_product(client, name="<script>alert(1)</script>")
    async with database_factory() as database:
        product = (await database.execute(select(Product))).scalar_one()

    response = await client.get(f"/products/{product.id}")
    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text


@pytest.mark.asyncio
async def test_only_owner_can_edit_or_delete_product(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client, "seller")
    await create_product(client)
    async with database_factory() as database:
        product = (await database.execute(select(Product))).scalar_one()

    await signup_and_login(client, "intruder")
    edit_response = await client.get(f"/products/{product.id}/edit")
    page = await client.get("/")
    delete_response = await client.post(
        f"/products/{product.id}/delete",
        data={"csrf_token": csrf_token(page.text)},
    )

    assert edit_response.status_code == 404
    assert delete_response.status_code == 404
    async with database_factory() as database:
        unchanged = await database.get(Product, product.id)
    assert unchanged is not None
    assert unchanged.status == ProductStatus.ACTIVE


@pytest.mark.asyncio
async def test_owner_can_update_product_and_replace_image(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client, "seller")
    await create_product(client)
    async with database_factory() as database:
        product = (await database.execute(select(Product))).scalar_one()
        product_id = product.id
        old_image_key = product.image_key

    page = await client.get(f"/products/{product_id}/edit")
    response = await client.post(
        f"/products/{product_id}/edit",
        data={
            "name": "수정된 카메라",
            "description": "수정된 설명",
            "price": "200000",
            "csrf_token": csrf_token(page.text),
        },
        files={"image": ("new.jpg", image_bytes("JPEG"), "image/jpeg")},
    )
    assert response.status_code == 303

    async with database_factory() as database:
        updated = await database.get(Product, product_id)
    assert updated is not None
    assert updated.name == "수정된 카메라"
    assert updated.price == 200000
    assert updated.image_key.endswith(".jpg")
    assert not (upload_dir() / old_image_key).exists()
    assert (upload_dir() / updated.image_key).is_file()


@pytest.mark.asyncio
async def test_owner_soft_deletes_product_and_image(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client, "seller")
    await create_product(client)
    async with database_factory() as database:
        product = (await database.execute(select(Product))).scalar_one()
        product_id = product.id
        image_key = product.image_key

    detail = await client.get(f"/products/{product_id}")
    response = await client.post(
        f"/products/{product_id}/delete",
        data={"csrf_token": csrf_token(detail.text)},
    )
    assert response.status_code == 303

    async with database_factory() as database:
        deleted = await database.get(Product, product_id)
    assert deleted is not None
    assert deleted.status == ProductStatus.DELETED
    assert deleted.deleted_at is not None
    assert not (upload_dir() / image_key).exists()
    assert (await client.get(f"/products/{product_id}")).status_code == 404
    assert (await client.get(f"/product-images/{image_key}")).status_code == 404
