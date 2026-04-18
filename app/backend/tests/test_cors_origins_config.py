"""
Tests for CORS_ORIGINS config parsing.

Verifies:
  - default value used when CORS_ORIGINS is not set
  - single origin parsed correctly
  - multiple origins parsed with whitespace stripped
  - empty parts filtered out (trailing comma, empty string)
  - multiple comma-separated origins work
"""

import importlib
import os
from unittest.mock import patch

# Preserve required env vars that config.py checks at import time
_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
    "JWT_SECRET": "test-secret-please-do-not-use-in-prod",
    "SUPADATA_API_KEY": "test-supadata-key",
    "YOUTUBE_CHANNEL_ID": "UC_testchannel",
    "CHANNEL_SYNC_TYPE": "video",
}


def test_cors_origins_default_when_not_set():
    """Default includes localhost and 127.0.0.1 on FRONTEND_PORT."""
    env = {k: v for k, v in _REQUIRED_ENV.items()}
    with patch.dict(os.environ, env, clear=True):
        import backend.config as config_module
        importlib.reload(config_module)
        assert "http://localhost:5173" in config_module.CORS_ORIGINS
        assert "http://127.0.0.1:5173" in config_module.CORS_ORIGINS


def test_cors_origins_single_value():
    """Single origin is parsed into a list correctly."""
    env = {k: v for k, v in _REQUIRED_ENV.items()}
    env["CORS_ORIGINS"] = "https://example.com"
    with patch.dict(os.environ, env, clear=True):
        import backend.config as config_module
        importlib.reload(config_module)
        assert config_module.CORS_ORIGINS == ["https://example.com"]


def test_cors_origins_whitespace_stripped():
    """Origins with surrounding whitespace are stripped."""
    env = {k: v for k, v in _REQUIRED_ENV.items()}
    env["CORS_ORIGINS"] = "  https://foo.com ,  https://bar.com  "
    with patch.dict(os.environ, env, clear=True):
        import backend.config as config_module
        importlib.reload(config_module)
        assert config_module.CORS_ORIGINS == ["https://foo.com", "https://bar.com"]


def test_cors_origins_empty_parts_filtered():
    """Empty strings from empty segments are filtered out."""
    env = {k: v for k, v in _REQUIRED_ENV.items()}
    env["CORS_ORIGINS"] = "https://valid.com,,,"
    with patch.dict(os.environ, env, clear=True):
        import backend.config as config_module
        importlib.reload(config_module)
        assert config_module.CORS_ORIGINS == ["https://valid.com"]
        assert "" not in config_module.CORS_ORIGINS


def test_cors_origins_multiple():
    """Multiple comma-separated origins work."""
    env = {k: v for k, v in _REQUIRED_ENV.items()}
    env["CORS_ORIGINS"] = "https://a.com,https://b.com,https://c.com"
    with patch.dict(os.environ, env, clear=True):
        import backend.config as config_module
        importlib.reload(config_module)
        assert len(config_module.CORS_ORIGINS) == 3
        assert "https://a.com" in config_module.CORS_ORIGINS
        assert "https://b.com" in config_module.CORS_ORIGINS
        assert "https://c.com" in config_module.CORS_ORIGINS


def test_cors_origins_trailing_comma():
    """Trailing comma produces no empty entry."""
    env = {k: v for k, v in _REQUIRED_ENV.items()}
    env["CORS_ORIGINS"] = "https://foo.com,"
    with patch.dict(os.environ, env, clear=True):
        import backend.config as config_module
        importlib.reload(config_module)
        assert config_module.CORS_ORIGINS == ["https://foo.com"]
        assert "" not in config_module.CORS_ORIGINS
