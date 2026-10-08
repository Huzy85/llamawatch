"""Tests for _safe_resolve_docs — path traversal and symlink protection."""

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from llamawatch.routes_framework import _safe_resolve_docs


def _patch_roots(roots: list[Path]):
    """Monkeypatch _docs_all_roots to return the given paths with dummy labels."""
    return mock.patch(
        "llamawatch.routes_framework._docs_all_roots",
        return_value=[(r, str(r)) for r in roots],
    )


# ── No docs roots configured ──────────────────────────────────────────────────

def test_no_roots_returns_none(tmp_path):
    with _patch_roots([]):
        assert _safe_resolve_docs(str(tmp_path / "anything.md")) is None


# ── Path inside a valid root ──────────────────────────────────────────────────

def test_path_inside_root_is_returned(tmp_path):
    doc = tmp_path / "notes.md"
    doc.write_text("# hello")
    with _patch_roots([tmp_path]):
        result = _safe_resolve_docs(str(doc))
    assert result == doc.resolve()


def test_subdirectory_inside_root_is_returned(tmp_path):
    sub = tmp_path / "sub" / "deep.md"
    sub.parent.mkdir()
    sub.write_text("deep")
    with _patch_roots([tmp_path]):
        result = _safe_resolve_docs(str(sub))
    assert result is not None


# ── Path traversal attempts ───────────────────────────────────────────────────

def test_path_outside_all_roots_returns_none(tmp_path):
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    with _patch_roots([docs_root]):
        result = _safe_resolve_docs(str(tmp_path / "secret.txt"))
    assert result is None


def test_dotdot_traversal_returns_none(tmp_path):
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    attack = str(docs_root / ".." / ".." / "etc" / "passwd")
    with _patch_roots([docs_root]):
        result = _safe_resolve_docs(attack)
    assert result is None


def test_absolute_path_outside_root_returns_none(tmp_path):
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    with _patch_roots([docs_root]):
        result = _safe_resolve_docs("/etc/passwd")
    assert result is None


# ── Symlink protection ────────────────────────────────────────────────────────

def test_symlink_pointing_outside_root_returns_none(tmp_path):
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    # Create a target dir OUTSIDE the docs root
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")

    # Symlink inside the docs root that points outside
    link = docs_root / "evil_link"
    os.symlink(outside, link)

    with _patch_roots([docs_root]):
        # Requesting the target via the symlink should be blocked
        result = _safe_resolve_docs(str(link / "secret.txt"))
    assert result is None


def test_symlink_within_root_is_allowed(tmp_path):
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    real_file = docs_root / "real.md"
    real_file.write_text("content")
    link = docs_root / "alias.md"
    os.symlink(real_file, link)

    with _patch_roots([docs_root]):
        result = _safe_resolve_docs(str(link))
    assert result is not None


# ── Multiple roots ────────────────────────────────────────────────────────────

def test_path_in_second_root_is_found(tmp_path):
    root1 = tmp_path / "r1"
    root2 = tmp_path / "r2"
    root1.mkdir()
    root2.mkdir()
    doc = root2 / "doc.md"
    doc.write_text("hello")

    with _patch_roots([root1, root2]):
        result = _safe_resolve_docs(str(doc))
    assert result is not None


def test_path_not_in_any_root_returns_none(tmp_path):
    root1 = tmp_path / "r1"
    root2 = tmp_path / "r2"
    root1.mkdir()
    root2.mkdir()
    outside = tmp_path / "evil.md"
    outside.write_text("!")

    with _patch_roots([root1, root2]):
        result = _safe_resolve_docs(str(outside))
    assert result is None
