from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import auth
from app.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_throttle():
    auth.reset_throttle_state()
    yield
    auth.reset_throttle_state()


@pytest.fixture
def settings(tmp_path) -> Settings:
    """An in-memory station with a real passphrase, so auth is genuinely enforced."""
    return Settings(
        _env_file=None,
        QUIETWAVE_STORE="memory",
        QUIETWAVE_MEDIA_ROOT=str(tmp_path / "media"),
        QUIETWAVE_COOKIE_SECURE=False,
        QUIETWAVE_VALIDATE_TWILIO_SIGNATURE=False,
        PUBLIC_BASE_URL="https://text.example.com",
        APP_PASSPHRASE="correct-horse-battery",
        SESSION_SECRET="test-secret-not-used-anywhere-real-000000",
    )


@pytest_asyncio.fixture
async def app(settings):
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="https://text.example.com"
    ) as http:
        yield http


@pytest_asyncio.fixture
async def signed_in(client):
    response = await client.post(
        "/auth/login", json={"passphrase": "correct-horse-battery"}
    )
    assert response.status_code == 200, response.text
    return client


@pytest_asyncio.fixture
async def line(app):
    return await app.state.store.create_line("+15551110000", "Test line", True)


@pytest_asyncio.fixture
async def thread(app, line):
    return await app.state.store.create_thread(line["id"], "+15552220000", "NIGHTJAR")
