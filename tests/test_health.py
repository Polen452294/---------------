from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient

from booking_bot.db.session import get_session
from booking_bot.main import create_app


class FakeSession:
    async def execute(self, _statement) -> None:
        return None


async def test_liveness() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_with_available_database() -> None:
    app = create_app()

    async def override_session() -> AsyncIterator[FakeSession]:
        yield FakeSession()

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
