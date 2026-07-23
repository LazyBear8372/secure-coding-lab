import io
import re
import warnings
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ("JPEG", "jpg"),
    "image/png": ("PNG", "png"),
    "image/webp": ("WEBP", "webp"),
}
IMAGE_KEY_PATTERN = re.compile(r"[0-9a-f]{32}\.(?:jpg|png|webp)")


class InvalidProductImage(ValueError):
    pass


async def read_image(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidProductImage("대표 사진은 5MB 이하로 업로드해 주세요.")
    if not data:
        raise InvalidProductImage("대표 사진을 선택해 주세요.")
    return data


def sanitize_image(data: bytes, content_type: str | None) -> tuple[Image.Image, str, str]:
    expected = ALLOWED_IMAGE_TYPES.get(content_type or "")
    if expected is None:
        raise InvalidProductImage("JPEG, PNG, WebP 형식의 이미지만 업로드할 수 있습니다.")

    expected_format, extension = expected
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                if source.format != expected_format:
                    raise InvalidProductImage("파일 형식과 실제 이미지 내용이 일치하지 않습니다.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise InvalidProductImage("이미지 해상도가 너무 큽니다.")
                image = ImageOps.exif_transpose(source)
                if expected_format == "JPEG":
                    sanitized = image.convert("RGB")
                elif image.mode not in {"RGB", "RGBA"}:
                    sanitized = image.convert("RGBA")
                else:
                    sanitized = image.copy()
    except InvalidProductImage:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
    ):
        raise InvalidProductImage("올바른 이미지 파일을 업로드해 주세요.") from None

    return sanitized, expected_format, extension


async def save_product_image(upload: UploadFile, upload_dir: Path) -> str:
    data = await read_image(upload)
    image, image_format, extension = sanitize_image(data, upload.content_type)
    image_key = f"{uuid4().hex}.{extension}"
    upload_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = upload_dir / f".{image_key}.tmp"
    destination = upload_dir / image_key
    try:
        save_options = (
            {"quality": 85, "optimize": True} if image_format != "PNG" else {"optimize": True}
        )
        image.save(temporary_path, format=image_format, **save_options)
        temporary_path.replace(destination)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise InvalidProductImage("대표 사진을 저장하지 못했습니다.") from None
    finally:
        image.close()
    return image_key


def image_path(upload_dir: Path, image_key: str) -> Path | None:
    if IMAGE_KEY_PATTERN.fullmatch(image_key) is None:
        return None
    return upload_dir / image_key


def delete_product_image(upload_dir: Path, image_key: str) -> None:
    path = image_path(upload_dir, image_key)
    if path is not None:
        path.unlink(missing_ok=True)
