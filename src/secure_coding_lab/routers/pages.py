from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from secure_coding_lab.auth import get_optional_user
from secure_coding_lab.config import Settings, get_settings
from secure_coding_lab.models import User
from secure_coding_lab.security import make_csrf_token
from secure_coding_lab.templating import templates
from secure_coding_lab.web_security import set_csrf_cookie

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    csrf_token = make_csrf_token(settings.secret_key)
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": "Secure Coding Lab",
            "current_user": user,
            "csrf_token": csrf_token,
        },
    )
    set_csrf_cookie(response, csrf_token, settings)
    return response


@router.get("/partials/status", response_class=HTMLResponse)
async def status_partial() -> HTMLResponse:
    return HTMLResponse('<p class="status status--ok">애플리케이션이 정상 작동 중입니다.</p>')
