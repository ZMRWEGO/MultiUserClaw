"""File preview helpers for the web API.

Decides how to render a workspace file to the browser:
- text/markdown/json: inline content
- image/pdf/html/docx/xlsx: download_url (browser/JS handles)
- binary: download only
"""

from __future__ import annotations

import json as _json
import mimetypes
from pathlib import Path

# Max bytes returned inline for text-based previews. Larger files are truncated.
TEXT_PREVIEW_LIMIT = 5 * 1024 * 1024  # 5 MB

# Map file extension -> (preview_kind, language)
# language is only meaningful when preview_kind == "text"
_EXT_MAP: dict[str, tuple[str, str | None]] = {
    # markdown
    ".md": ("markdown", None),
    ".markdown": ("markdown", None),
    # structured text
    ".json": ("json", None),
    ".html": ("html", None),
    ".htm": ("html", None),
    # pdf
    ".pdf": ("pdf", None),
    # images
    ".png": ("image", None),
    ".jpg": ("image", None),
    ".jpeg": ("image", None),
    ".gif": ("image", None),
    ".webp": ("image", None),
    ".svg": ("image", None),
    ".bmp": ("image", None),
    ".ico": ("image", None),
    # office (modern)
    ".docx": ("docx", None),
    ".xlsx": ("xlsx", None),
    # legacy office and other binary-y formats
    ".doc": ("binary", None),
    ".xls": ("binary", None),
    ".ppt": ("binary", None),
    ".pptx": ("binary", None),
    ".odt": ("binary", None),
    ".ods": ("binary", None),
    ".odp": ("binary", None),
    ".rtf": ("binary", None),
    # archives
    ".zip": ("binary", None),
    ".tar": ("binary", None),
    ".gz": ("binary", None),
    ".tgz": ("binary", None),
    ".bz2": ("binary", None),
    ".7z": ("binary", None),
    ".rar": ("binary", None),
    # source code (text + language hint)
    ".py": ("text", "python"),
    ".ts": ("text", "typescript"),
    ".tsx": ("text", "tsx"),
    ".js": ("text", "javascript"),
    ".jsx": ("text", "jsx"),
    ".mjs": ("text", "javascript"),
    ".cjs": ("text", "javascript"),
    ".go": ("text", "go"),
    ".rs": ("text", "rust"),
    ".java": ("text", "java"),
    ".kt": ("text", "kotlin"),
    ".swift": ("text", "swift"),
    ".c": ("text", "c"),
    ".cpp": ("text", "cpp"),
    ".cc": ("text", "cpp"),
    ".cxx": ("text", "cpp"),
    ".h": ("text", "c"),
    ".hpp": ("text", "cpp"),
    ".cs": ("text", "csharp"),
    ".rb": ("text", "ruby"),
    ".php": ("text", "php"),
    ".lua": ("text", "lua"),
    ".css": ("text", "css"),
    ".scss": ("text", "scss"),
    ".less": ("text", "less"),
    ".yaml": ("text", "yaml"),
    ".yml": ("text", "yaml"),
    ".toml": ("text", "toml"),
    ".sh": ("text", "bash"),
    ".bash": ("text", "bash"),
    ".zsh": ("text", "bash"),
    ".fish": ("text", "bash"),
    ".sql": ("text", "sql"),
    ".xml": ("text", "xml"),
    ".dockerfile": ("text", "dockerfile"),
    ".env": ("text", "env"),
    ".ini": ("text", "ini"),
    ".cfg": ("text", "ini"),
    ".conf": ("text", "ini"),
    # plain text
    ".txt": ("text", "plain"),
    ".log": ("text", "plain"),
    ".csv": ("text", "csv"),
    ".tsv": ("text", "csv"),
}


def _binary_reason_for(ext: str) -> str:
    """Categorize why a file is treated as binary (only).

    'office_legacy' -> .doc/.xls/.ppt/.pptx/.odt/.ods/.odp/.rtf
    'archive'       -> .zip/.tar/.gz/.7z/.rar/.bz2/.tgz
    'unknown'       -> anything else (e.g. .exe, .bin, no extension w/o text content)
    """
    if ext in {".doc", ".xls", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf"}:
        return "office_legacy"
    if ext in {".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar"}:
        return "archive"
    return "unknown"


def _looks_like_text(sample: bytes) -> bool:
    """Sniff a small sample to see if it's likely UTF-8 text.

    Returns True if the bytes decode cleanly as UTF-8 and don't contain too many
    non-printable / non-whitespace control characters.
    """
    try:
        decoded = sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    # Allow tab, newline, carriage return, plus printable >= 0x20.
    bad = sum(1 for ch in decoded if ord(ch) < 0x20 and ch not in "\t\n\r")
    return bad < max(1, len(decoded) // 50)  # < 2% control chars


def preview_kind_for(path: Path) -> tuple[str, str | None]:
    """Return (preview_kind, language|None) for the given file.

    preview_kind ∈ {text, markdown, json, html, image, pdf, docx, xlsx, binary}
    language is only meaningful for kind == "text".
    """
    ext = path.suffix.lower()

    # Special case: extension-less files like 'Dockerfile', 'Makefile', 'AGENTS.md' (already covered)
    if not ext:
        name_lower = path.name.lower()
        if name_lower == "dockerfile":
            return ("text", "dockerfile")
        if name_lower in {"makefile", "gnumakefile"}:
            return ("text", "makefile")
        # Sniff content
        try:
            with path.open("rb") as f:
                sample = f.read(4096)
            if _looks_like_text(sample):
                return ("text", "plain")
        except OSError:
            pass
        return ("binary", None)

    if ext in _EXT_MAP:
        return _EXT_MAP[ext]

    # Unknown extension — try to infer via mimetype
    ct, _ = mimetypes.guess_type(path.name)
    if ct:
        if ct.startswith("text/"):
            return ("text", "plain")
        if ct.startswith("image/"):
            return ("image", None)
        if ct == "application/pdf":
            return ("pdf", None)
        if ct == "application/json":
            return ("json", None)
        if ct in {"application/xml", "text/xml"}:
            return ("text", "xml")

    return ("binary", None)


def read_text_safely(
    path: Path, max_bytes: int = TEXT_PREVIEW_LIMIT
) -> tuple[str, bool]:
    """Read text content with a byte limit.

    Returns (content, truncated). If file > max_bytes, only the first max_bytes
    are returned and truncated=True. Decoding uses UTF-8 with replacement on
    error so that broken bytes don't crash preview for mostly-ASCII files.
    """
    size = path.stat().st_size
    truncated = size > max_bytes
    n = min(size, max_bytes)
    with path.open("rb") as f:
        raw = f.read(n)
    return raw.decode("utf-8", errors="replace"), truncated


def pretty_print_json(text: str) -> str:
    """Pretty-print JSON. Falls back to original text if parse fails."""
    try:
        obj = _json.loads(text)
        return _json.dumps(obj, ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        return text
