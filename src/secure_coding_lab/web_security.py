from fastapi import Request
from starlette.responses import Response

from secure_coding_lab.auth import CSRF_COOKIE_NAME
from secure_coding_lab.config import Settings
from secure_coding_lab.security import is_valid_csrf_token, make_csrf_token
from secure_coding_lab.templating import templates


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


def render_with_csrf(
    request: Request,
    template_name: str,
    *,
    settings: Settings,
    context: dict[str, object] | None = None,
    status_code: int = 200,
) -> Response:
    csrf_token = make_csrf_token(settings.secret_key)
    template_context = {"csrf_token": csrf_token, **(context or {})}
    response = templates.TemplateResponse(
        request=request,
        name=template_name,
        context=template_context,
        status_code=status_code,
    )
    set_csrf_cookie(response, csrf_token, settings)
    return response
