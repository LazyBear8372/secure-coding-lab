from fastapi import Request
from starlette.responses import Response

from secure_coding_lab.auth import CSRF_COOKIE_NAME
from secure_coding_lab.config import Settings
from secure_coding_lab.security import is_valid_csrf_token


def cookie_is_secure(settings: Settings) -> bool:
    return settings.app_env == "production"


def set_csrf_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=60 * 60,
        httponly=True,
        secure=cookie_is_secure(settings),
        samesite="lax",
        path="/",
    )


def csrf_is_valid(request: Request, csrf_token: str, settings: Settings) -> bool:
    return is_valid_csrf_token(
        csrf_token,
        request.cookies.get(CSRF_COOKIE_NAME),
        settings.secret_key,
    )
