"""Tests for the /api/version endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app
import backend.routes.version as version_module


@pytest.mark.asyncio
async def test_version_returns_correct_version(monkeypatch):
    """GET /api/version returns the actual package version when metadata is available."""
    # Arrange: mock the version function in the routes.version module namespace
    monkeypatch.setattr(version_module, "version", lambda name: "1.2.3")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/version")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "1.2.3"


@pytest.mark.asyncio
async def test_version_fallback_on_package_not_found(monkeypatch):
    """GET /api/version returns '0.1.0' when package metadata is unavailable."""
    from importlib.metadata import PackageNotFoundError

    def raise_not_found(name):
        raise PackageNotFoundError(f"Package {name!r} not found")

    # Arrange: mock version to raise PackageNotFoundError in the routes.version module namespace
    monkeypatch.setattr(version_module, "version", raise_not_found)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/version")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_version_is_non_empty_string():
    """The version field is a non-empty string regardless of the source."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/version")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0
