"""Tests for ``nanobot.web.archive`` (folder-download zip jobs).

Module-level + integration coverage for tasks 2.8 and 3.6.
"""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanobot.web import archive


@pytest.fixture(autouse=True)
def _clear_jobs():
    archive._jobs.clear()
    yield
    archive._jobs.clear()


def _populate_workspace(workspace: Path) -> Path:
    """Create a sample directory structure under ``workspace``."""
    sub = workspace / "subdir"
    sub.mkdir()
    (sub / "a.txt").write_bytes(b"hello\n")
    (sub / "b.bin").write_bytes(b"\x00" * 4096)
    nested = sub / "nested"
    nested.mkdir()
    (nested / "c.md").write_bytes(b"# header\n" + b"x" * 8192)
    return sub


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------


def test_create_job_happy_path(tmp_path: Path):
    target = _populate_workspace(tmp_path)

    job = archive.create_job(tmp_path, "subdir")

    assert job.job_id  # non-empty hex
    assert job.status == "pending"
    assert job.rel_path == "subdir"
    assert job.target_dir.is_dir()
    assert job.target_dir.parent == tmp_path / ".cache" / "archive"
    assert job.total_bytes_estimate == sum(
        f.stat().st_size for f in target.rglob("*") if f.is_file()
    )
    assert archive.get_job(job.job_id) is job


def test_create_job_rejects_file_path(tmp_path: Path):
    (tmp_path / "notes.md").write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        archive.create_job(tmp_path, "notes.md")


def test_create_job_rejects_traversal(tmp_path: Path):
    with pytest.raises(ValueError, match="outside workspace"):
        archive.create_job(tmp_path, "../etc/passwd")


def test_create_job_rejects_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        archive.create_job(tmp_path, "does_not_exist")


def test_create_job_rejects_empty_path(tmp_path: Path):
    with pytest.raises(ValueError, match="required"):
        archive.create_job(tmp_path, "")


# ---------------------------------------------------------------------------
# compression lifecycle
# ---------------------------------------------------------------------------


async def test_start_job_runs_to_ready(tmp_path: Path):
    _populate_workspace(tmp_path)
    job = archive.create_job(tmp_path, "subdir")

    task = await archive.start_job(tmp_path, job, ttl_seconds=600)
    await task  # wait for compression + cleanup-scheduling to finish

    assert job.status == "ready"
    assert job.bytes_written > 0
    assert job.bytes_written <= job.total_bytes_estimate
    assert job.zip_path is not None and job.zip_path.exists()
    assert job.zip_path.name == "subdir.zip"


async def test_zip_entries_are_relative_to_source(tmp_path: Path):
    source = _populate_workspace(tmp_path)
    job = archive.create_job(tmp_path, "subdir")
    task = await archive.start_job(tmp_path, job, ttl_seconds=600)
    await task

    with zipfile.ZipFile(job.zip_path) as zf:
        names = set(zf.namelist())
    expected = {
        str(p.relative_to(source))
        for p in source.rglob("*")
        if p.is_file()
    }
    assert names == expected
    assert all(not n.startswith("subdir/") for n in names)


async def test_bytes_written_monotonic(tmp_path: Path):
    # Use many small files so we can sample mid-flight.
    src = tmp_path / "many"
    src.mkdir()
    for i in range(50):
        (src / f"f{i}.txt").write_bytes(b"x" * 1024)

    job = archive.create_job(tmp_path, "many")
    task = await archive.start_job(tmp_path, job, ttl_seconds=600)

    samples: list[int] = []
    while not task.done():
        samples.append(job.bytes_written)
        await asyncio.sleep(0)
    samples.append(job.bytes_written)
    await task

    # Samples must be non-decreasing.
    assert all(b <= a for a, b in zip(samples[1:], samples[:-1])) or all(
        a <= b for a, b in zip(samples, samples[1:])
    ), samples


async def test_failed_job_keeps_dir_until_ttl(tmp_path: Path, monkeypatch):
    # Force _compress to fail.
    def boom(job, source):
        job.status = "failed"
        job.error = "boom"

    monkeypatch.setattr(archive, "_compress", boom)
    _populate_workspace(tmp_path)
    job = archive.create_job(tmp_path, "subdir")
    target_dir = job.target_dir
    assert target_dir.exists()

    task = await archive.start_job(tmp_path, job, ttl_seconds=0)
    await task
    # Allow the call_later(0) to fire.
    await asyncio.sleep(0.05)

    # After TTL=0 cleanup, the directory should be removed.
    assert not target_dir.exists()
    assert archive.get_job(job.job_id) is None
    assert job.status == "failed"
    assert job.error == "boom"


async def test_ttl_cleanup_removes_dir(tmp_path: Path):
    _populate_workspace(tmp_path)
    job = archive.create_job(tmp_path, "subdir")
    target_dir = job.target_dir

    task = await archive.start_job(tmp_path, job, ttl_seconds=0)
    await task
    await asyncio.sleep(0.05)

    assert not target_dir.exists()
    assert archive.get_job(job.job_id) is None


def test_delete_job_unknown_returns_false(tmp_path: Path):
    assert archive.delete_job("does_not_exist") is False


def test_purge_residue_clears_leftover(tmp_path: Path):
    leftover = tmp_path / ".cache" / "archive" / "abc123"
    leftover.mkdir(parents=True)
    (leftover / "stale.zip").write_bytes(b"x")

    archive.purge_residue(tmp_path)

    assert not leftover.exists()
    # Parent dir is OK to leave behind; what matters is per-job dirs are gone.


def test_purge_residue_when_root_missing(tmp_path: Path):
    # Should not raise.
    archive.purge_residue(tmp_path)


# ---------------------------------------------------------------------------
# HTTP integration via TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch):
    """Build a nanobot FastAPI app pointed at a tmp workspace."""
    from nanobot.config.schema import Config
    from nanobot.web.server import create_app

    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    cfg.proxy.url = "http://stub"
    cfg.proxy.token = "stub"
    assert cfg.is_proxy_mode  # skip provider key validation

    app = create_app(config=cfg)
    return TestClient(app), tmp_path


def test_post_archive_creates_job(app_client):
    client, workspace = app_client
    _populate_workspace(workspace)

    r = client.post("/api/workspace/archive", json={"path": "subdir"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert "job_id" in body and len(body["job_id"]) >= 12


def test_post_archive_rejects_file(app_client):
    client, workspace = app_client
    (workspace / "notes.md").write_text("x", encoding="utf-8")

    r = client.post("/api/workspace/archive", json={"path": "notes.md"})
    assert r.status_code == 400
    assert "not a directory" in r.json()["detail"].lower()


def test_post_archive_rejects_traversal(app_client):
    client, _ = app_client
    r = client.post("/api/workspace/archive", json={"path": "../etc/passwd"})
    assert r.status_code == 400


def test_post_archive_rejects_missing(app_client):
    client, _ = app_client
    r = client.post("/api/workspace/archive", json={"path": "missing"})
    assert r.status_code == 404


def test_get_status_unknown_job(app_client):
    client, _ = app_client
    r = client.get("/api/workspace/archive/deadbeefdeadbeef")
    assert r.status_code == 404


def test_get_status_progress_and_download(app_client):
    client, workspace = app_client
    _populate_workspace(workspace)

    r = client.post("/api/workspace/archive", json={"path": "subdir"})
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    # Poll until ready (TestClient runs in a worker thread; the asyncio task
    # the route scheduled finishes quickly for small inputs, but we still poll
    # to mirror real client behavior).
    for _ in range(200):
        s = client.get(f"/api/workspace/archive/{job_id}").json()
        if s["status"] in ("ready", "failed"):
            break
    else:
        pytest.fail("archive job never finished")
    assert s["status"] == "ready", s
    assert s["bytes_written"] > 0
    assert "error" in s

    # Download
    d = client.get(f"/api/workspace/archive/{job_id}/download")
    assert d.status_code == 200
    assert d.headers["content-type"] == "application/zip"
    assert "subdir.zip" in d.headers["content-disposition"]
    assert d.content[:2] == b"PK"  # zip magic


def test_download_before_ready(app_client, monkeypatch):
    """Force a job to stay in 'running' so download returns 409."""
    client, workspace = app_client
    _populate_workspace(workspace)

    # Patch _compress so the job hangs in 'running'.
    started = asyncio.Event()
    finish = asyncio.Event()

    async def hang(job, source):
        job.status = "running"
        started.set()
        # Simulate work that doesn't finish until we say so.
        # (Run sync to_thread compatibility: just spin via time.sleep.)
        import time as _time
        while not finish.is_set():
            _time.sleep(0.01)

    # _compress is sync; replace with a sync no-finish stub.
    def hang_sync(job, source):
        job.status = "running"
        # Don't set ready/failed/finished_at — leaves status at running.

    monkeypatch.setattr(archive, "_compress", hang_sync)

    r = client.post("/api/workspace/archive", json={"path": "subdir"})
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    # Give the background task a chance to start.
    for _ in range(50):
        s = client.get(f"/api/workspace/archive/{job_id}").json()
        if s["status"] == "running":
            break

    d = client.get(f"/api/workspace/archive/{job_id}/download")
    assert d.status_code == 409
    assert "not ready" in d.json()["detail"].lower()


def test_download_unknown_job(app_client):
    client, _ = app_client
    r = client.get("/api/workspace/archive/deadbeef00/download")
    assert r.status_code == 404


def test_legacy_download_directory_returns_400(app_client):
    """GET /api/workspace/download on a directory must point users to /archive."""
    client, workspace = app_client
    _populate_workspace(workspace)

    r = client.get("/api/workspace/download", params={"path": "subdir"})
    assert r.status_code == 400
    assert "/archive" in r.json()["detail"]


def test_legacy_download_file_unchanged(app_client):
    """GET /api/workspace/download on a file must continue to work."""
    client, workspace = app_client
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")

    r = client.get("/api/workspace/download", params={"path": "hello.txt"})
    assert r.status_code == 200
    assert r.content == b"hello"
    assert "hello.txt" in r.headers["content-disposition"]
