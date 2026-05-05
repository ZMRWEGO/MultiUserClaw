"""File storage helpers for the web API."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote


def content_disposition(disposition: str, filename: str) -> str:
    """Build Content-Disposition header value, RFC 5987 encoding for non-ASCII."""
    try:
        filename.encode("ascii")
        return f'{disposition}; filename="{filename}"'
    except UnicodeEncodeError:
        utf8_quoted = quote(filename)
        return f"{disposition}; filename*=UTF-8''{utf8_quoted}"

from loguru import logger


def _is_safe_filename(filename: str) -> bool:
    """Check if filename is safe (no path separators or dot-prefixed)."""
    return bool(filename) and "/" not in filename and "\\" not in filename and not filename.startswith(".")


def _is_safe_file_id(file_id: str) -> bool:
    """Ensure file_id contains only hex characters."""
    return bool(file_id) and all(c in '0123456789abcdef' for c in file_id)


def _files_dir(workspace: Path) -> Path:
    """Return the files storage directory, creating it if needed."""
    d = workspace / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_file_id() -> str:
    """Generate a short unique file ID (12 hex chars)."""
    return uuid.uuid4().hex[:12]


def save_file(
    workspace: Path,
    file_id: str,
    filename: str,
    content: bytes,
    content_type: str,
    session_id: str = "web:default",
) -> dict[str, Any]:
    """Save a file to workspace/files/<file_id>/ and write metadata.json."""
    if not _is_safe_filename(filename):
        raise ValueError(f"Invalid filename: {filename}")
    file_dir = _files_dir(workspace) / file_id
    file_dir.mkdir(parents=True, exist_ok=True)

    file_path = file_dir / filename
    file_path.write_bytes(content)

    metadata = {
        "file_id": file_id,
        "name": filename,
        "content_type": content_type,
        "size": len(content),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
    }
    (file_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    return metadata


def get_file_metadata(workspace: Path, file_id: str) -> dict[str, Any] | None:
    """Load metadata for a file. Returns None if not found or invalid."""
    if not _is_safe_file_id(file_id):
        return None
    meta_path = _files_dir(workspace) / file_id / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"Corrupted metadata file: {meta_path}")
        return None


def get_file_path(workspace: Path, file_id: str) -> Path | None:
    """Get the actual file path for a file_id. Returns None if not found."""
    meta = get_file_metadata(workspace, file_id)
    if meta is None:
        return None
    file_path = _files_dir(workspace) / file_id / meta["name"]
    # Ensure resolved path is within files directory
    try:
        file_path.resolve().relative_to(_files_dir(workspace).resolve())
    except ValueError:
        return None
    return file_path if file_path.exists() else None


def list_files(workspace: Path, session_id: str | None = None) -> list[dict[str, Any]]:
    """List all file metadata, optionally filtered by session_id."""
    files_dir = _files_dir(workspace)
    result = []
    for entry in sorted(files_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta_path = entry / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            continue
        if session_id and meta.get("session_id") != session_id:
            continue
        result.append(meta)
    return result


def delete_file(workspace: Path, file_id: str) -> bool:
    """Delete a file and its metadata. Returns True if deleted."""
    if not _is_safe_file_id(file_id):
        return False
    file_dir = _files_dir(workspace) / file_id
    if not file_dir.exists():
        return False
    shutil.rmtree(file_dir)
    return True


# ---------------------------------------------------------------------------
# Workspace browser helpers (browse the entire workspace directory)
# ---------------------------------------------------------------------------

import mimetypes


def _resolve_workspace_path(workspace: Path, rel_path: str) -> Path | None:
    """Resolve a relative path within workspace, rejecting traversal."""
    workspace = workspace.resolve()
    target = (workspace / rel_path).resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        return None
    return target


def browse_workspace(workspace: Path, rel_path: str = "") -> dict[str, Any]:
    """List contents of a directory within the workspace."""
    workspace = workspace.resolve()
    target = _resolve_workspace_path(workspace, rel_path)
    if target is None or not target.is_dir():
        raise ValueError("Invalid directory path")

    items: list[dict[str, Any]] = []
    try:
        entries = sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        raise ValueError("Permission denied")

    for entry in entries:
        # Skip hidden files/dirs
        if entry.name.startswith("."):
            continue
        rel = str(entry.relative_to(workspace))
        if entry.is_dir():
            items.append({
                "name": entry.name,
                "path": rel,
                "type": "directory",
                "size": None,
                "modified": datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
        elif entry.is_file():
            stat = entry.stat()
            ct, _ = mimetypes.guess_type(entry.name)
            items.append({
                "name": entry.name,
                "path": rel,
                "type": "file",
                "size": stat.st_size,
                "content_type": ct or "application/octet-stream",
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return {
        "path": str(target.relative_to(workspace)) if target != workspace else "",
        "items": items,
    }


def workspace_file_path(workspace: Path, rel_path: str) -> Path | None:
    """Resolve a file path within workspace for download."""
    target = _resolve_workspace_path(workspace, rel_path)
    if target is None or not target.is_file():
        return None
    return target


def save_to_workspace(workspace: Path, rel_dir: str, filename: str, content: bytes) -> dict[str, Any]:
    """Save uploaded file to a specific directory in the workspace."""
    workspace = workspace.resolve()
    target_dir = _resolve_workspace_path(workspace, rel_dir)
    if target_dir is None:
        raise ValueError("Invalid directory path")
    target_dir.mkdir(parents=True, exist_ok=True)

    file_path = (target_dir / filename).resolve()
    try:
        file_path.relative_to(workspace)
    except ValueError:
        raise ValueError("Invalid filename")

    file_path.write_bytes(content)
    stat = file_path.stat()
    ct, _ = mimetypes.guess_type(filename)
    return {
        "name": filename,
        "path": str(file_path.relative_to(workspace)),
        "type": "file",
        "size": stat.st_size,
        "content_type": ct or "application/octet-stream",
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def delete_workspace_path(workspace: Path, rel_path: str) -> bool:
    """Delete a file or directory from the workspace."""
    target = _resolve_workspace_path(workspace, rel_path)
    if target is None or not target.exists():
        return False
    # Don't allow deleting the workspace root
    if target == workspace.resolve():
        return False
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return True


def create_workspace_dir(workspace: Path, rel_path: str) -> dict[str, Any]:
    """Create a directory in the workspace."""
    workspace = workspace.resolve()
    target = _resolve_workspace_path(workspace, rel_path)
    if target is None:
        raise ValueError("Invalid directory path")
    target.mkdir(parents=True, exist_ok=True)
    return {
        "name": target.name,
        "path": str(target.relative_to(workspace)),
        "type": "directory",
    }


# ---------------------------------------------------------------------------
# Workspace file search (for chat input @-mention picker)
# ---------------------------------------------------------------------------

# Directories to skip when walking workspace for search candidates. Same list
# used by `WorkspaceWatcher` so users get consistent behavior across features.
_NOISE_DIRS = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
})

_QUERY_MAX_LEN = 100
_LIMIT_MAX = 50
_LIMIT_DEFAULT = 20


def _fuzzy_match(needle: str, haystack: str) -> bool:
    """Return True if every char of needle appears in haystack in order."""
    if not needle:
        return True
    i = 0
    for c in haystack:
        if c == needle[i]:
            i += 1
            if i == len(needle):
                return True
    return False


def _match_score(name: str, path: str, q: str) -> int | None:
    """Rank a candidate against query. Lower is better. None = no match.

    Tiers (rank ascending = better):
      0 name-prefix       (q='rep' → 'report.md')
      1 name-substring    (q='ort' → 'report.md')
      2 path-prefix       (q='docs/' → 'docs/foo.md')
      3 path-substring    (q='ort' → 'docs/report.md' when name didn't match)
      4 fuzzy             (q='rpm' → 'report.md')
    Empty query yields rank=99 (sorted purely by mtime later).
    """
    if not q:
        return 99
    n_lower = name.lower()
    p_lower = path.lower()
    if n_lower.startswith(q):
        return 0
    if q in n_lower:
        return 1
    if p_lower.startswith(q):
        return 2
    if q in p_lower:
        return 3
    if _fuzzy_match(q, n_lower) or _fuzzy_match(q, p_lower):
        return 4
    return None


def _iter_preview_files(workspace: Path) -> Iterator[dict]:
    """Walk workspace, yielding dicts for every preview-able file.

    - Prunes hidden dirs (starting with '.') and entries in `_NOISE_DIRS`.
    - Skips hidden files (starting with '.').
    - Skips files whose `preview_kind` is 'binary'.
    - Skips files that fail to stat (race with deletion).
    """
    from nanobot.web.preview import preview_kind_for

    workspace_resolved = workspace.resolve()

    for root, dirs, files in os.walk(workspace_resolved, followlinks=False):
        # In-place prune: hidden + noise dirs are never recursed into.
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in _NOISE_DIRS
        ]
        root_path = Path(root)
        for fname in files:
            if fname.startswith("."):
                continue
            fpath = root_path / fname
            try:
                kind, _ = preview_kind_for(fpath)
            except Exception:
                continue
            if kind == "binary":
                continue
            try:
                stat = fpath.stat()
            except OSError:
                continue
            try:
                rel = fpath.relative_to(workspace_resolved).as_posix()
            except ValueError:
                continue
            ct, _ = mimetypes.guess_type(fname)
            yield {
                "name": fname,
                "path": rel,
                "size": stat.st_size,
                "content_type": ct or "application/octet-stream",
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                "preview_kind": kind,
                "_mtime_ts": stat.st_mtime,
            }


def search_workspace_files(
    workspace: Path,
    query: str = "",
    limit: int = _LIMIT_DEFAULT,
) -> dict[str, Any]:
    """Return the top-N preview-able files matching `query`.

    Sorted by `(rank asc, mtime desc)`. Ranks: see `_match_score`.
    `query` is trimmed and lowercased; length capped to 100.
    `limit` is clamped to `[1, 50]`.
    """
    q = (query or "").strip().lower()[:_QUERY_MAX_LEN]
    limit = max(1, min(_LIMIT_MAX, limit))

    matches: list[tuple[int, float, dict]] = []
    for item in _iter_preview_files(workspace):
        score = _match_score(item["name"], item["path"], q)
        if score is None:
            continue
        # Negate mtime so that higher mtime sorts first under ascending sort.
        matches.append((score, -item["_mtime_ts"], item))

    matches.sort(key=lambda t: (t[0], t[1]))
    total = len(matches)
    truncated = total > limit
    items = [m[2] for m in matches[:limit]]
    for it in items:
        it.pop("_mtime_ts", None)

    return {
        "items": items,
        "total": total,
        "truncated": truncated,
    }
