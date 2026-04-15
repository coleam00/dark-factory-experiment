"""
Unit tests for config module — CORS_ORIGINS parsing.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

# Ensure backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Re-import config to pick up env manipulation
import importlib
import backend.config as cfg


@pytest.fixture(autouse=True)
def clean_env():
    """Save and restore CORS_ORIGINS env var around each test."""
    original = os.environ.get("CORS_ORIGINS")
    yield
    if original is None:
        os.environ.pop("CORS_ORIGINS", None)
    else:
        os.environ["CORS_ORIGINS"] = original


def test_cors_origins_default():
    """Default includes localhost variants at the configured frontend port."""
    os.environ.pop("CORS_ORIGINS", None)
    importlib.reload(cfg)
    assert cfg.CORS_ORIGINS == [
        f"http://localhost:{cfg.FRONTEND_PORT}",
        f"http://127.0.0.1:{cfg.FRONTEND_PORT}",
    ]


def test_cors_origins_custom():
    """Custom env var is parsed as comma-separated list."""
    os.environ["CORS_ORIGINS"] = "http://localhost:9999,http://127.0.0.1:9999"
    importlib.reload(cfg)
    assert cfg.CORS_ORIGINS == ["http://localhost:9999", "http://127.0.0.1:9999"]


def test_cors_origins_single():
    """Single origin (no comma) becomes a one-element list."""
    os.environ["CORS_ORIGINS"] = "https://example.com"
    importlib.reload(cfg)
    assert cfg.CORS_ORIGINS == ["https://example.com"]


def test_cors_origins_strips_whitespace():
    """Origins with surrounding whitespace are trimmed."""
    os.environ["CORS_ORIGINS"] = "  http://localhost:7000 ,  http://127.0.0.1:7000  "
    importlib.reload(cfg)
    assert cfg.CORS_ORIGINS == ["http://localhost:7000", "http://127.0.0.1:7000"]


def test_cors_origins_empty_elements_filtered():
    """Empty elements from double-commas are filtered out."""
    os.environ["CORS_ORIGINS"] = "http://localhost:8000,,http://127.0.0.1:8000"
    importlib.reload(cfg)
    assert cfg.CORS_ORIGINS == ["http://localhost:8000", "http://127.0.0.1:8000"]