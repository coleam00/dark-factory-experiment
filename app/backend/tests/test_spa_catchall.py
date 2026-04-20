"""Tests for the SPA catch-all route serve_spa_or_static."""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest.fixture
def frontend_dist_empty(monkeypatch):
    """FRONTEND_DIST is unset — tests the bare fallback branch."""
    monkeypatch.setattr("backend.main._frontend_dist", "")


@pytest.fixture
def frontend_dist_with_static(monkeypatch, tmp_path):
    """FRONTEND_DIST points at a temp dir with a hashed JS asset."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "assets").mkdir()
    (dist / "assets" / "index-[hash].js").write_text("console.log('built');")
    (dist / "index.html").write_text("<!DOCTYPE html>")
    monkeypatch.setattr("backend.main._frontend_dist", str(dist))


@pytest.fixture
def frontend_dist_with_index(monkeypatch, tmp_path):
    """FRONTEND_DIST points at a temp dir with only index.html (no assets subdir)."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!DOCTYPE html>")
    monkeypatch.setattr("backend.main._frontend_dist", str(dist))


class TestServeSpaOrStatic:
    """Tests for GET /{path:path} catch-all."""

    async def test_api_path_returns_404(self):
        """Paths starting with api/ must be blocked with 404."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/nonexistent-route")
        assert response.status_code == 404

    async def test_bare_api_path_returns_404(self, frontend_dist_empty):
        """Bare /api without trailing slash should also be blocked."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api")
        assert response.status_code == 404

    async def test_serves_static_file_when_exists(self, frontend_dist_with_static):
        """When FRONTEND_DIST is set and the file exists, it is served."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("assets/index-[hash].js")
        assert response.status_code == 200
        assert response.text == "console.log('built');"

    async def test_falls_back_to_index_html_for_unknown_path(self, frontend_dist_with_static):
        """When the path is not a file, index.html is served (not 404)."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("c/some-conversation-id")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text

    async def test_falls_back_to_index_html_when_frontend_dist_unset(self, frontend_dist_empty, monkeypatch, tmp_path):
        """When FRONTEND_DIST is unset, index.html is served from cwd if it exists."""
        # Create a temp index.html at cwd so this test is not affected by test environment
        cwd_index = tmp_path / "index.html"
        cwd_index.write_text("<!DOCTYPE html>")
        monkeypatch.chdir(tmp_path)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("login")
        assert response.status_code == 200

    async def test_nonexistent_static_file_falls_back_to_index(self, frontend_dist_with_index):
        """When FRONTEND_DIST is set but file doesn't exist, index.html is served."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("assets/nonexistent.js")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text


class TestServeRoot:
    """Tests for GET / (root path)."""

    async def test_root_serves_index_html(self, frontend_dist_empty, monkeypatch, tmp_path):
        """Root path / should serve index.html."""
        cwd_index = tmp_path / "index.html"
        cwd_index.write_text("<!DOCTYPE html>")
        monkeypatch.chdir(tmp_path)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
        assert response.status_code == 200

    async def test_root_serves_index_html_from_dist(self, frontend_dist_with_index):
        """Root path / should serve index.html from FRONTEND_DIST when set."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text
