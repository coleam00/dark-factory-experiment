"""Pure-function tests for the Dynamous content parser.

The DB side of `ingest_dynamous_content` requires a real Postgres pool and is
covered by smoke tests at deploy time. Here we verify the markdown-parsing
helpers in isolation.
"""

from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from backend.ingest.dynamous import _hash_body, _parse_frontmatter, _parse_segments


def test_parse_frontmatter_extracts_typed_pairs():
    text = """---
title: "Module 1: Intro"
lesson_id: 2103795
course_slug: module-1
---

Body here.
"""
    fm, body = _parse_frontmatter(text)
    assert fm["title"] == "Module 1: Intro"
    assert fm["lesson_id"] == "2103795"
    assert fm["course_slug"] == "module-1"
    assert body.strip() == "Body here."


def test_parse_frontmatter_no_frontmatter_returns_empty():
    text = "Just a plain markdown body, no frontmatter."
    fm, body = _parse_frontmatter(text)
    assert fm == {}
    assert body == text


def test_parse_segments_with_timestamp_headings():
    body = """## [00:00:00] Intro

Welcome to the course.

## [00:02:15] Architecture

The agent has three layers.

## [00:05:30] Setup

Install the dependencies.
"""
    segs = _parse_segments(body)
    assert len(segs) == 3

    assert segs[0]["start"] == 0.0
    assert segs[0]["end"] == 135.0  # 00:02:15
    assert segs[0]["heading"] == "Intro"
    assert "Welcome to the course." in segs[0]["text"]

    assert segs[1]["start"] == 135.0
    assert segs[1]["end"] == 330.0  # 00:05:30
    assert "three layers" in segs[1]["text"]

    # Final segment: end == start (no successor to compute duration from).
    assert segs[2]["start"] == 330.0
    assert segs[2]["end"] == 330.0
    assert "Install" in segs[2]["text"]


def test_parse_segments_no_headings_yields_one_segment():
    body = "No headings, just a flat transcript blob."
    segs = _parse_segments(body)
    assert len(segs) == 1
    assert segs[0]["start"] == 0.0
    assert segs[0]["text"] == body


def test_parse_segments_empty_body_yields_zero():
    assert _parse_segments("") == []
    assert _parse_segments("\n   \n") == []


def test_hash_body_is_stable_and_distinct():
    a = _hash_body("hello world")
    b = _hash_body("hello world")
    c = _hash_body("hello world!")
    assert a == b
    assert a != c
    assert len(a) == 64  # sha256 hex
