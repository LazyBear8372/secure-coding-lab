import io
import re
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.main import app
from secure_coding_lab.models import Product, ProductStatus, User, UserStatus
from secure_coding_lab.product_images import MAX_IMAGE_BYTES
from secure_coding_lab.security import hash_password

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


def make_product(seller: User, name: str, status: ProductStatus = ProductStatus.ACTIVE) -> Product:
    return Product(
        seller_id=seller.id,
        name=name,
        description=f"{name} 설명",
        price=1000,
        image_key=f"{uuid4().hex}.png",
        status=status,
    )


@pytest.mark.asyncio
async def test_product_list_is_paginated(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with database_factory() as database:
        seller = User(username="seller", password_hash=hash_password(PASSWORD))
        database.add(seller)
        await database.flush()
        database.add_all([make_product(seller, f"상품 {number:02}") for number in range(13)])
        await database.commit()

    first_page = await client.get("/products")
    second_page = await client.get("/products?page=2")
    assert first_page.status_code == 200
    assert first_page.text.count('class="product-card"') == 12
    assert "다음" in first_page.text
    assert second_page.text.count('class="product-card"') == 1


@pytest.mark.asyncio
async def test_product_search_treats_wildcards_as_literals(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with database_factory() as database:
        seller = User(username="seller", password_hash=hash_password(PASSWORD))
        database.add(seller)
        await database.flush()
        database.add_all(
            [
                make_product(seller, "할인율 100% 상품"),
                make_product(seller, "할인율 1000 상품"),
            ]
        )
        await database.commit()

    response = await client.get("/products", params={"q": "%"})
    assert response.status_code == 200
    assert "할인율 100% 상품" in response.text
    assert "할인율 1000 상품" not in response.text


@pytest.mark.asyncio
async def test_public_list_applies_status_and_seller_exposure_policy(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with database_factory() as database:
        active_seller = User(username="active-seller", password_hash=hash_password(PASSWORD))
        suspended_seller = User(
            username="suspended-seller",
            password_hash=hash_password(PASSWORD),
            status=UserStatus.SUSPENDED,
        )
        database.add_all([active_seller, suspended_seller])
        await database.flush()
        database.add_all(
            [
                make_product(active_seller, "공개 판매 중", ProductStatus.ACTIVE),
                make_product(active_seller, "공개 판매 완료", ProductStatus.SOLD),
                make_product(active_seller, "비공개 차단", ProductStatus.BLOCKED),
                make_product(active_seller, "비공개 삭제", ProductStatus.DELETED),
                make_product(suspended_seller, "정지 판매자 상품", ProductStatus.ACTIVE),
            ]
        )
        await database.commit()

    response = await client.get("/products")
    assert response.status_code == 200
    assert "공개 판매 중" in response.text
    assert "공개 판매 완료" in response.text
    assert "비공개 차단" not in response.text
    assert "비공개 삭제" not in response.text
    assert "정지 판매자 상품" not in response.text


@pytest.mark.asyncio
async def test_my_products_only_shows_owned_non_deleted_products(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client, "seller")
    async with database_factory() as database:
        seller = (
            await database.execute(select(User).where(User.username == "seller"))
        ).scalar_one()
        other = User(username="other", password_hash=hash_password(PASSWORD))
        database.add(other)
        await database.flush()
        database.add_all(
            [
                make_product(seller, "내 판매 중", ProductStatus.ACTIVE),
                make_product(seller, "내 판매 완료", ProductStatus.SOLD),
                make_product(seller, "내 차단 상품", ProductStatus.BLOCKED),
                make_product(seller, "내 삭제 상품", ProductStatus.DELETED),
                make_product(other, "다른 판매자 상품", ProductStatus.ACTIVE),
            ]
        )
        await database.commit()

    response = await client.get("/me/products")
    assert response.status_code == 200
    assert "내 판매 중" in response.text
    assert "내 판매 완료" in response.text
    assert "내 차단 상품" in response.text
    assert "내 삭제 상품" not in response.text
    assert "다른 판매자 상품" not in response.text


@pytest.mark.asyncio
async def test_owner_can_mark_active_product_sold_once(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client, "seller")
    await create_product(client)
    async with database_factory() as database:
        product = (await database.execute(select(Product))).scalar_one()
        product_id = product.id

    detail = await client.get(f"/products/{product_id}")
    response = await client.post(
        f"/products/{product_id}/sold",
        data={"csrf_token": csrf_token(detail.text)},
    )
    assert response.status_code == 303
    async with database_factory() as database:
        sold = await database.get(Product, product_id)
    assert sold is not None
    assert sold.status == ProductStatus.SOLD
    assert "판매 완료" in (await client.get(f"/products/{product_id}")).text

    detail = await client.get(f"/products/{product_id}")
    repeated = await client.post(
        f"/products/{product_id}/sold",
        data={"csrf_token": csrf_token(detail.text)},
    )
    assert repeated.status_code == 404


@pytest.mark.asyncio
async def test_non_owner_cannot_mark_product_sold(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client, "seller")
    await create_product(client)
    async with database_factory() as database:
        product = (await database.execute(select(Product))).scalar_one()
        product_id = product.id

    await signup_and_login(client, "intruder")
    page = await client.get("/")
    response = await client.post(
        f"/products/{product_id}/sold",
        data={"csrf_token": csrf_token(page.text)},
    )
    assert response.status_code == 404
    async with database_factory() as database:
        unchanged = await database.get(Product, product_id)
    assert unchanged is not None
    assert unchanged.status == ProductStatus.ACTIVE


@pytest.mark.asyncio
async def test_mark_sold_rejects_forged_csrf(
    client: AsyncClient,
    database_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_and_login(client, "seller")
    await create_product(client)
    async with database_factory() as database:
        product = (await database.execute(select(Product))).scalar_one()
        product_id = product.id

    response = await client.post(
        f"/products/{product_id}/sold",
        data={"csrf_token": "forged"},
    )
    assert response.status_code == 403
    async with database_factory() as database:
        unchanged = await database.get(Product, product_id)
    assert unchanged is not None
    assert unchanged.status == ProductStatus.ACTIVE


@pytest.mark.asyncio
async def test_product_list_rejects_unbounded_page_or_query(client: AsyncClient) -> None:
    assert (await client.get("/products?page=10001")).status_code == 422
    response = await client.get("/products", params={"q": "가" * 101})
    assert response.status_code == 400
