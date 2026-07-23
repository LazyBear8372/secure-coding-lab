import pytest
from httpx import ASGITransport, AsyncClient

from secure_coding_lab.main import app


@pytest.mark.asyncio
async def test_liveness() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
