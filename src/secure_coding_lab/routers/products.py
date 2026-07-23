from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from secure_coding_lab.auth import get_optional_user
from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.db import get_db_session
from secure_coding_lab.models import Product, ProductStatus, User, UserStatus
from secure_coding_lab.product_images import (
    InvalidProductImage,
    delete_product_image,
    image_path,
    save_product_image,
)
from secure_coding_lab.web_security import csrf_is_valid, render_with_csrf

router = APIRouter(include_in_schema=False)

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 5000
MAX_PRICE = 9_223_372_036_854_775_807
IMAGE_MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


def validate_fields(name: str, description: str, price: str) -> tuple[str, str, int]:
    normalized_name = name.strip()
    normalized_description = description.strip()
    if not normalized_name or len(normalized_name) > MAX_NAME_LENGTH:
        raise ValueError(f"상품명은 1자 이상 {MAX_NAME_LENGTH}자 이하로 입력해 주세요.")
    if not normalized_description or len(normalized_description) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(f"설명은 1자 이상 {MAX_DESCRIPTION_LENGTH}자 이하로 입력해 주세요.")
    try:
        parsed_price = int(price.strip())
    except ValueError:
        raise ValueError("가격은 0 이상의 정수로 입력해 주세요.") from None
    if not 0 <= parsed_price <= MAX_PRICE:
        raise ValueError("가격은 0 이상의 정수로 입력해 주세요.")
    return normalized_name, normalized_description, parsed_price


def form_context(
    *,
    name: str = "",
    description: str = "",
    price: str = "",
    product: Product | None = None,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "price": price,
        "product": product,
        "error": error,
    }


async def owned_product(database: AsyncSession, product_id: UUID, user: User) -> Product | None:
    result = await database.execute(
        select(Product).where(
            Product.id == product_id,
            Product.seller_id == user.id,
            Product.status != ProductStatus.DELETED,
        )
    )
    return result.scalar_one_or_none()


@router.get("/products/new", response_class=HTMLResponse)
async def new_product_page(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    return render_with_csrf(
        request,
        "product_form.html",
        settings=settings,
        context={"current_user": user, **form_context()},
    )


@router.post("/products", response_class=HTMLResponse)
async def create_product(
    request: Request,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()],
    price: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    image: Annotated[UploadFile, File()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    context = {
        "current_user": user,
        **form_context(name=name, description=description, price=price),
    }
    if not csrf_is_valid(request, csrf_token, settings):
        return render_with_csrf(
            request,
            "product_form.html",
            settings=settings,
            context={**context, "error": "요청을 확인할 수 없습니다."},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    try:
        normalized_name, normalized_description, parsed_price = validate_fields(
            name, description, price
        )
        image_key = await save_product_image(image, Path(settings.upload_dir))
    except (ValueError, InvalidProductImage) as exc:
        return render_with_csrf(
            request,
            "product_form.html",
            settings=settings,
            context={**context, "error": str(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    product = Product(
        seller_id=user.id,
        name=normalized_name,
        description=normalized_description,
        price=parsed_price,
        image_key=image_key,
    )
    database.add(product)
    try:
        await database.commit()
    except SQLAlchemyError:
        await database.rollback()
        delete_product_image(Path(settings.upload_dir), image_key)
        raise
    return RedirectResponse(f"/products/{product.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(
    request: Request,
    product_id: UUID,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    result = await database.execute(
        select(Product, User)
        .join(User, User.id == Product.seller_id)
        .where(
            Product.id == product_id,
            Product.status.in_([ProductStatus.ACTIVE, ProductStatus.SOLD]),
            User.status == UserStatus.ACTIVE,
        )
    )
    row = result.one_or_none()
    if row is None:
        return render_with_csrf(
            request,
            "product_not_found.html",
            settings=settings,
            context={"current_user": user},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    product, seller = row
    return render_with_csrf(
        request,
        "product_detail.html",
        settings=settings,
        context={"current_user": user, "product": product, "seller": seller},
    )


@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
async def edit_product_page(
    request: Request,
    product_id: UUID,
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    product = await owned_product(database, product_id, user)
    if product is None:
        return render_with_csrf(
            request,
            "product_not_found.html",
            settings=settings,
            context={"current_user": user},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return render_with_csrf(
        request,
        "product_form.html",
        settings=settings,
        context={
            "current_user": user,
            **form_context(
                name=product.name,
                description=product.description,
                price=str(product.price),
                product=product,
            ),
        },
    )


@router.post("/products/{product_id}/edit", response_class=HTMLResponse)
async def update_product(
    request: Request,
    product_id: UUID,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()],
    price: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    image: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    product = await owned_product(database, product_id, user)
    if product is None:
        return render_with_csrf(
            request,
            "product_not_found.html",
            settings=settings,
            context={"current_user": user},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    context = {
        "current_user": user,
        **form_context(name=name, description=description, price=price, product=product),
    }
    if not csrf_is_valid(request, csrf_token, settings):
        return render_with_csrf(
            request,
            "product_form.html",
            settings=settings,
            context={**context, "error": "요청을 확인할 수 없습니다."},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    new_image_key: str | None = None
    try:
        normalized_name, normalized_description, parsed_price = validate_fields(
            name, description, price
        )
        if image is not None and image.filename:
            new_image_key = await save_product_image(image, Path(settings.upload_dir))
    except (ValueError, InvalidProductImage) as exc:
        return render_with_csrf(
            request,
            "product_form.html",
            settings=settings,
            context={**context, "error": str(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    old_image_key = product.image_key
    product.name = normalized_name
    product.description = normalized_description
    product.price = parsed_price
    product.updated_at = datetime.now(UTC)
    if new_image_key is not None:
        product.image_key = new_image_key
    try:
        await database.commit()
    except SQLAlchemyError:
        await database.rollback()
        if new_image_key is not None:
            delete_product_image(Path(settings.upload_dir), new_image_key)
        raise
    if new_image_key is not None:
        delete_product_image(Path(settings.upload_dir), old_image_key)
    return RedirectResponse(f"/products/{product.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/products/{product_id}/delete", response_class=HTMLResponse)
async def delete_product(
    request: Request,
    product_id: UUID,
    csrf_token: Annotated[str, Form()],
    user: Annotated[User | None, Depends(get_optional_user)],
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    if user is None:
        return login_redirect()
    product = await owned_product(database, product_id, user)
    if product is None:
        return render_with_csrf(
            request,
            "product_not_found.html",
            settings=settings,
            context={"current_user": user},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if not csrf_is_valid(request, csrf_token, settings):
        return HTMLResponse("요청을 확인할 수 없습니다.", status_code=status.HTTP_403_FORBIDDEN)

    product.status = ProductStatus.DELETED
    product.deleted_at = datetime.now(UTC)
    product.updated_at = product.deleted_at
    await database.commit()
    delete_product_image(Path(settings.upload_dir), product.image_key)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/product-images/{image_key}", response_class=FileResponse)
async def product_image(
    image_key: str,
    database: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    path = image_path(Path(settings.upload_dir), image_key)
    if path is None or not path.is_file():
        return HTMLResponse("이미지를 찾을 수 없습니다.", status_code=status.HTTP_404_NOT_FOUND)
    result = await database.execute(
        select(Product.id).where(
            Product.image_key == image_key,
            Product.status.in_([ProductStatus.ACTIVE, ProductStatus.SOLD]),
        )
    )
    if result.scalar_one_or_none() is None:
        return HTMLResponse("이미지를 찾을 수 없습니다.", status_code=status.HTTP_404_NOT_FOUND)
    extension = path.suffix.removeprefix(".")
    return FileResponse(path, media_type=IMAGE_MEDIA_TYPES[extension])
