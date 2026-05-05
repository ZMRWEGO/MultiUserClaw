"""Workspace folder archive (zip) jobs.

Three states: ``pending`` → ``running`` → (``ready`` | ``failed``).

Lifecycle:
    create_job → start_job → background _compress → ready/failed
After completion (success or failure) the temp directory is scheduled for
removal at ``+TTL_SECONDS`` from finish; on a successful download the route
removes it immediately via ``BackgroundTask``.

Job state lives in a process-local dict; it is intentionally not persisted —
the user container is short-lived and a restart wiping in-flight jobs is the
expected behavior.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from loguru import logger

ARCHIVE_TTL_SECONDS = int(os.environ.get("NANOBOT_ARCHIVE_TTL_SECONDS", "600"))

JobStatus = Literal["pending", "running", "ready", "failed"]


@dataclass
class ArchiveJob:
    job_id: str
    rel_path: str
    target_dir: Path  # workspace/.cache/archive/<job_id>/
    zip_path: Path | None = None
    status: JobStatus = "pending"
    bytes_written: int = 0
    total_bytes_estimate: int = 0
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


_jobs: dict[str, ArchiveJob] = {}


def _archive_root(workspace: Path) -> Path:
    return workspace.resolve() / ".cache" / "archive"


def _resolve_under(workspace: Path, rel_path: str) -> Path | None:
    workspace = workspace.resolve()
    target = (workspace / rel_path).resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        return None
    return target


def _estimate_total_bytes(target: Path) -> int:
    total = 0
    for f in target.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                continue
    return total


def create_job(workspace: Path, rel_path: str) -> ArchiveJob:
    """Create and register a new archive job; caller must call ``start_job``.

    Raises:
        ValueError: rel_path is empty, traverses outside workspace, or is not a directory.
        FileNotFoundError: rel_path does not exist on disk.
    """
    if not rel_path or not rel_path.strip():
        raise ValueError("Path is required")

    target = _resolve_under(workspace, rel_path)
    if target is None:
        raise ValueError("Path is outside workspace")
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {rel_path}")
    if not target.is_dir():
        raise ValueError("Path is not a directory")

    job_id = uuid.uuid4().hex
    job_dir = _archive_root(workspace) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    rel = str(target.relative_to(workspace.resolve()))
    job = ArchiveJob(
        job_id=job_id,
        rel_path=rel,
        target_dir=job_dir,
        total_bytes_estimate=_estimate_total_bytes(target),
    )
    _jobs[job_id] = job
    return job


def _compress(job: ArchiveJob, source: Path) -> None:
    """Synchronous zip compression. Runs in a worker thread."""
    job.status = "running"
    zip_path = job.target_dir / f"{source.name}.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in source.rglob("*"):
                if fp.is_file():
                    arcname = str(fp.relative_to(source))
                    zf.write(fp, arcname)
                    try:
                        job.bytes_written += fp.stat().st_size
                    except OSError:
                        pass
        job.zip_path = zip_path
        job.status = "ready"
    except Exception as e:  # pragma: no cover - exercised via test that triggers it
        logger.exception("Archive job {} failed", job.job_id)
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = datetime.now(timezone.utc)


async def start_job(
    workspace: Path,
    job: ArchiveJob,
    *,
    ttl_seconds: int | None = None,
) -> asyncio.Task:
    """Schedule the compression task; returns the asyncio.Task running it.

    The task drives ``_compress`` in a thread (zipfile is sync I/O), then
    arms a TTL cleanup via ``loop.call_later``. The TTL fires whether the
    job ended in ``ready`` or ``failed``; failure cleanup is delayed so the
    error can be inspected.
    """
    source = _resolve_under(workspace, job.rel_path)
    if source is None or not source.is_dir():
        job.status = "failed"
        job.error = "Source path no longer accessible"
        job.finished_at = datetime.now(timezone.utc)
        return asyncio.create_task(asyncio.sleep(0))

    ttl = ttl_seconds if ttl_seconds is not None else ARCHIVE_TTL_SECONDS

    async def _run() -> None:
        await asyncio.to_thread(_compress, job, source)
        loop = asyncio.get_running_loop()
        loop.call_later(ttl, lambda: delete_job(job.job_id))

    return asyncio.create_task(_run())


def get_job(job_id: str) -> ArchiveJob | None:
    return _jobs.get(job_id)


def delete_job(job_id: str) -> bool:
    """Remove a job from the registry and best-effort delete its temp dir."""
    job = _jobs.pop(job_id, None)
    if job is None:
        return False
    try:
        if job.target_dir.exists():
            shutil.rmtree(job.target_dir)
    except OSError:
        logger.warning("delete_job: could not remove {}", job.target_dir)
    return True


def purge_residue(workspace: Path) -> None:
    """Remove leftover archive temp dirs at startup.

    Synchronous on purpose so it can be invoked during ``create_app`` before
    the FastAPI server begins accepting requests.
    """
    root = _archive_root(workspace)
    if not root.exists():
        return
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            shutil.rmtree(entry)
        except OSError:
            logger.warning("purge_residue: could not remove {}", entry)
