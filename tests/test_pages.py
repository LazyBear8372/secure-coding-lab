import pytest
from httpx import ASGITransport, AsyncClient

from secure_coding_lab.main import app


@pytest.mark.asyncio
async def test_index_page() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "안전한 중고거래" in response.text


@pytest.mark.asyncio
async def test_htmx_status_partial() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/partials/status")

    assert response.status_code == 200
    assert "정상 작동 중" in response.text
