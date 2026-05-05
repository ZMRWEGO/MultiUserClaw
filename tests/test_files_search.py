"""Tests for `search_workspace_files` (chat input @-mention backend)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from nanobot.web.files import (
    _fuzzy_match,
    _match_score,
    search_workspace_files,
)


# ---------------------------------------------------------------------------
# _fuzzy_match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle, haystack, expected",
    [
        ("rep", "report.md", True),
        ("rpt", "report.md", True),     # in-order chars
        ("rom", "report.md", True),     # r..o.....m
        ("xyz", "report.md", False),
        ("", "anything", True),         # empty needle always matches
        ("report", "report.md", True),
        ("portmd", "report.md", True),
        ("zport", "report.md", False),  # 'z' before 'p' not in haystack
    ],
)
def test_fuzzy_match(needle: str, haystack: str, expected: bool):
    assert _fuzzy_match(needle, haystack) is expected


# ---------------------------------------------------------------------------
# _match_score
# ---------------------------------------------------------------------------


def test_match_score_empty_query():
    assert _match_score("foo.md", "docs/foo.md", "") == 99


def test_match_score_name_prefix_beats_substring():
    # 'rep' is a prefix of 'report.md'
    assert _match_score("report.md", "docs/report.md", "rep") == 0
    # 'ort' is a substring inside the name but not a prefix
    assert _match_score("report.md", "docs/report.md", "ort") == 1


def test_match_score_path_prefix():
    # 'docs/' is a prefix of the *path*, but not contained in the name
    assert _match_score("foo.md", "docs/foo.md", "docs/") == 2


def test_match_score_path_substring():
    # 'i/foo' is a substring inside the path; name is just 'foo.md'
    assert _match_score("foo.md", "api/foo.md", "i/foo") == 3


def test_match_score_fuzzy_fallback():
    # 'rpm' is fuzzy-but-not-substring on 'report.md'
    score = _match_score("report.md", "docs/report.md", "rpm")
    assert score == 4


def test_match_score_no_match():
    assert _match_score("report.md", "docs/report.md", "xyzzz") is None


def test_match_score_case_insensitive():
    # Caller is responsible for lowercasing q; verify name/path comparison itself
    # works on lowercase against pre-lowered query in the helper.
    assert _match_score("REPORT.MD", "DOCS/REPORT.MD", "rep") == 0


# ---------------------------------------------------------------------------
# search_workspace_files — fixtures
# ---------------------------------------------------------------------------


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def populated_workspace(tmp_path: Path) -> Path:
    """Workspace with a few preview-able + ignored files."""
    # Preview-able
    _touch(tmp_path / "docs" / "report.md", "# Report\n")
    _touch(tmp_path / "docs" / "replan.md", "# Replan\n")
    _touch(tmp_path / "src" / "api" / "replicate.ts", "export {}\n")
    _touch(tmp_path / "src" / "main.py", "print('hi')\n")
    _touch(tmp_path / "README.md", "# readme\n")
    # Binary — should be excluded
    _touch(tmp_path / "bin" / "legacy.zip", "PKfake")
    _touch(tmp_path / "bin" / "old.doc", "doc")
    # Hidden — should be excluded
    _touch(tmp_path / ".env", "SECRET=1\n")
    _touch(tmp_path / ".hidden_dir" / "x.md", "h\n")
    # Noise dirs — should be excluded
    _touch(tmp_path / "node_modules" / "lodash" / "index.js", "noop\n")
    _touch(tmp_path / ".git" / "HEAD", "ref: x\n")
    _touch(tmp_path / "__pycache__" / "x.cpython-311.pyc", "")
    _touch(tmp_path / "dist" / "bundle.js", "noop\n")
    return tmp_path


# ---------------------------------------------------------------------------
# search_workspace_files — behavior
# ---------------------------------------------------------------------------


def test_search_returns_only_previewable(populated_workspace: Path):
    res = search_workspace_files(populated_workspace, query="", limit=50)
    paths = {it["path"] for it in res["items"]}
    # Preview-able files present
    assert "docs/report.md" in paths
    assert "docs/replan.md" in paths
    assert "src/api/replicate.ts" in paths
    assert "src/main.py" in paths
    assert "README.md" in paths


def test_search_excludes_binary(populated_workspace: Path):
    res = search_workspace_files(populated_workspace, query="", limit=50)
    paths = {it["path"] for it in res["items"]}
    assert "bin/legacy.zip" not in paths
    assert "bin/old.doc" not in paths


def test_search_excludes_hidden(populated_workspace: Path):
    res = search_workspace_files(populated_workspace, query="", limit=50)
    paths = {it["path"] for it in res["items"]}
    assert ".env" not in paths
    assert all(not p.startswith(".") for p in paths)


def test_search_excludes_noise_dirs(populated_workspace: Path):
    res = search_workspace_files(populated_workspace, query="", limit=50)
    paths = {it["path"] for it in res["items"]}
    assert all("node_modules/" not in p for p in paths)
    assert all(".git/" not in p for p in paths)
    assert all("__pycache__/" not in p for p in paths)
    assert all("dist/" not in p for p in paths)


def test_search_name_prefix_first(populated_workspace: Path):
    """`rep` should rank `report.md` and `replan.md` ahead of `replicate.ts`
    only by mtime; all three are name-prefix hits (rank=0)."""
    res = search_workspace_files(populated_workspace, query="rep", limit=10)
    names = [it["name"] for it in res["items"]]
    assert "report.md" in names
    assert "replan.md" in names
    assert "replicate.ts" in names


def test_search_path_prefix_when_name_does_not_match(populated_workspace: Path):
    res = search_workspace_files(populated_workspace, query="docs/", limit=10)
    paths = [it["path"] for it in res["items"]]
    assert paths
    # All hits should start with `docs/`
    assert all(p.startswith("docs/") for p in paths)


def test_search_fuzzy_fallback(populated_workspace: Path):
    """`rpt` is fuzzy on `report.md` but not substring."""
    res = search_workspace_files(populated_workspace, query="rpt", limit=10)
    paths = [it["path"] for it in res["items"]]
    assert "docs/report.md" in paths


def test_search_empty_query_sorts_by_mtime_desc(tmp_path: Path):
    """Empty query → all rank=99, ordered by mtime desc."""
    older = tmp_path / "older.md"
    newer = tmp_path / "newer.md"
    _touch(older, "old")
    time.sleep(0.05)
    _touch(newer, "new")
    # Force mtimes
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))

    res = search_workspace_files(tmp_path, query="", limit=10)
    paths = [it["path"] for it in res["items"]]
    assert paths.index("newer.md") < paths.index("older.md")


def test_search_limit_clamped_low(tmp_path: Path):
    _touch(tmp_path / "a.md")
    _touch(tmp_path / "b.md")
    res = search_workspace_files(tmp_path, query="", limit=0)
    assert len(res["items"]) == 1  # clamped to 1


def test_search_limit_clamped_high(tmp_path: Path):
    for i in range(60):
        _touch(tmp_path / f"f{i:02d}.md")
    res = search_workspace_files(tmp_path, query="", limit=999)
    assert len(res["items"]) == 50  # clamped to 50
    assert res["total"] == 60
    assert res["truncated"] is True


def test_search_truncated_flag(tmp_path: Path):
    for i in range(15):
        _touch(tmp_path / f"f{i:02d}.md")
    res = search_workspace_files(tmp_path, query="", limit=10)
    assert len(res["items"]) == 10
    assert res["total"] == 15
    assert res["truncated"] is True


def test_search_not_truncated_when_under_limit(tmp_path: Path):
    _touch(tmp_path / "a.md")
    _touch(tmp_path / "b.md")
    res = search_workspace_files(tmp_path, query="", limit=10)
    assert res["total"] == 2
    assert res["truncated"] is False


def test_search_query_length_truncated(tmp_path: Path):
    """Queries > 100 chars should be silently truncated, not error."""
    _touch(tmp_path / "abc.md")
    long_q = "a" * 200
    # Should not raise
    res = search_workspace_files(tmp_path, query=long_q, limit=10)
    # 'a' * 100 still matches 'abc.md' via fuzzy? No — fuzzy needs every char in order.
    # Actually 'a'*100 won't fit in 'abc.md'. So result is empty, but call must succeed.
    assert isinstance(res["items"], list)


def test_search_returned_paths_are_relative(populated_workspace: Path):
    res = search_workspace_files(populated_workspace, query="", limit=50)
    for it in res["items"]:
        # No absolute paths, no `..`
        assert not it["path"].startswith("/")
        assert ".." not in it["path"].split("/")


def test_search_response_shape(populated_workspace: Path):
    res = search_workspace_files(populated_workspace, query="rep", limit=5)
    assert "items" in res
    assert "total" in res
    assert "truncated" in res
    if res["items"]:
        item = res["items"][0]
        assert "name" in item
        assert "path" in item
        assert "size" in item
        assert "content_type" in item
        assert "modified" in item
        assert "preview_kind" in item
        # Internal field stripped
        assert "_mtime_ts" not in item


def test_search_case_insensitive_query(populated_workspace: Path):
    res_lower = search_workspace_files(populated_workspace, query="REP", limit=10)
    paths_lower = [it["path"] for it in res_lower["items"]]
    assert "docs/report.md" in paths_lower


def test_search_handles_missing_workspace(tmp_path: Path):
    """Workspace that doesn't exist should not crash — return empty list."""
    fake = tmp_path / "does_not_exist"
    res = search_workspace_files(fake, query="", limit=10)
    assert res["items"] == []
    assert res["total"] == 0


def test_search_followlinks_false_avoids_loops(tmp_path: Path):
    """A symlink loop must not hang the walker (followlinks=False)."""
    real = tmp_path / "real.md"
    _touch(real, "x")
    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    # symlink pointing back to its own parent
    try:
        (loop_dir / "back").symlink_to(tmp_path)
    except OSError:
        pytest.skip("symlinks not supported on this platform")
    # Should complete in finite time
    res = search_workspace_files(tmp_path, query="", limit=10)
    paths = {it["path"] for it in res["items"]}
    assert "real.md" in paths
