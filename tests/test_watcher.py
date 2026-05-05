"""Tests for the WorkspaceWatcher (Phase 2)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanobot.web.watcher import WorkspaceWatcher, _SKIP_DIRS


# Give watchdog enough time to actually fire events. macOS FSEvents has
# coalescing latency around 1s — use a generous timeout in tests.
_EVENT_TIMEOUT = 3.0


async def _wait_for_event(
    queue: asyncio.Queue,
    *,
    predicate=None,
    timeout: float = _EVENT_TIMEOUT,
):
    """Wait for an event matching predicate (or any if None) on the queue."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return None
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        if predicate is None or predicate(ev):
            return ev


@pytest.mark.asyncio
async def test_watcher_emits_create_event(tmp_path: Path):
    watcher = WorkspaceWatcher(tmp_path, debounce_seconds=0.05)
    queue = watcher.add_listener()
    try:
        # Give observer a beat to start
        await asyncio.sleep(0.2)

        target = tmp_path / "new.txt"
        target.write_text("hi", encoding="utf-8")

        ev = await _wait_for_event(
            queue,
            predicate=lambda e: e.get("path") == "new.txt"
            and e.get("event") in ("created", "modified"),
        )
        assert ev is not None, "expected create/modified event for new.txt"
        assert ev["type"] == "file_event"
        assert ev["is_directory"] is False
    finally:
        watcher.remove_listener(queue)
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_emits_delete_event(tmp_path: Path):
    target = tmp_path / "doomed.txt"
    target.write_text("bye", encoding="utf-8")

    watcher = WorkspaceWatcher(tmp_path, debounce_seconds=0.05)
    queue = watcher.add_listener()
    try:
        await asyncio.sleep(0.2)

        target.unlink()

        ev = await _wait_for_event(
            queue,
            predicate=lambda e: e.get("path") == "doomed.txt"
            and e.get("event") == "deleted",
        )
        assert ev is not None
    finally:
        watcher.remove_listener(queue)
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_skips_node_modules(tmp_path: Path):
    skip_dir = tmp_path / "node_modules"
    skip_dir.mkdir()

    watcher = WorkspaceWatcher(tmp_path, debounce_seconds=0.05)
    queue = watcher.add_listener()
    try:
        await asyncio.sleep(0.2)

        (skip_dir / "lib.js").write_text("console.log(1)", encoding="utf-8")

        # Should NOT receive an event for the skipped path
        ev = await _wait_for_event(
            queue,
            predicate=lambda e: "node_modules" in e.get("path", ""),
            timeout=1.0,
        )
        assert ev is None, f"expected no event for node_modules; got {ev}"
    finally:
        watcher.remove_listener(queue)
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_fanout_multiple_listeners(tmp_path: Path):
    watcher = WorkspaceWatcher(tmp_path, debounce_seconds=0.05)
    q1 = watcher.add_listener()
    q2 = watcher.add_listener()
    try:
        await asyncio.sleep(0.2)

        (tmp_path / "x.txt").write_text("1", encoding="utf-8")

        ev1 = await _wait_for_event(
            q1, predicate=lambda e: e.get("path") == "x.txt"
        )
        ev2 = await _wait_for_event(
            q2, predicate=lambda e: e.get("path") == "x.txt"
        )
        assert ev1 is not None
        assert ev2 is not None
    finally:
        watcher.remove_listener(q1)
        watcher.remove_listener(q2)
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_lazy_start_stop(tmp_path: Path):
    watcher = WorkspaceWatcher(tmp_path)
    # No observer until first listener
    assert watcher._observer is None

    q = watcher.add_listener()
    await asyncio.sleep(0.2)
    assert watcher._observer is not None
    assert watcher.listener_count == 1

    watcher.remove_listener(q)
    # After last listener removed, observer is stopped
    assert watcher._observer is None
    assert watcher.listener_count == 0


@pytest.mark.asyncio
async def test_watcher_debounce_collapses_modifies(tmp_path: Path):
    """Multiple modifies within the debounce window should collapse into 1 event."""
    target = tmp_path / "spam.txt"
    target.write_text("0", encoding="utf-8")

    watcher = WorkspaceWatcher(tmp_path, debounce_seconds=0.2)
    queue = watcher.add_listener()
    try:
        await asyncio.sleep(0.2)
        # Drain initial events
        while not queue.empty():
            queue.get_nowait()

        for i in range(5):
            target.write_text(str(i), encoding="utf-8")
            await asyncio.sleep(0.02)

        await asyncio.sleep(0.4)  # wait for debounce + processing

        # Count modify events for spam.txt
        count = 0
        while not queue.empty():
            ev = queue.get_nowait()
            if ev.get("path") == "spam.txt" and ev.get("event") == "modified":
                count += 1

        # Allow up to 2 (some FS may emit one before write completes)
        assert count <= 2, f"expected ≤2 collapsed modify events, got {count}"
    finally:
        watcher.remove_listener(queue)
        watcher.stop()


def test_skip_dirs_set_contains_common_noise():
    assert "node_modules" in _SKIP_DIRS
    assert ".git" in _SKIP_DIRS
    assert "__pycache__" in _SKIP_DIRS
    assert "dist" in _SKIP_DIRS


# ---------------------------------------------------------------------------
# /ws/files WebSocket endpoint integration
# ---------------------------------------------------------------------------


@pytest.fixture
def ws_client(tmp_path: Path):
    """Build a FastAPI app with a workspace watcher pointing at tmp_path."""
    from nanobot.config.schema import Config, ProxyConfig
    from nanobot.web.server import create_app
    from nanobot.bus.queue import MessageBus

    config = Config(proxy=ProxyConfig(url="http://localhost:9", token="t"))
    config.agents.defaults.workspace = str(tmp_path)
    app = create_app(bus=MessageBus(), config=config)
    return TestClient(app)


def test_ws_files_handshake_and_snapshot(ws_client, tmp_path: Path):
    """Client should receive a snapshot message right after connecting."""
    with ws_client.websocket_connect("/ws/files") as ws:
        # First message must be the snapshot signal
        msg = ws.receive_text()
        data = json.loads(msg)
        assert data == {"type": "snapshot"}


def test_ws_files_ping_pong(ws_client):
    with ws_client.websocket_connect("/ws/files") as ws:
        # Drain snapshot
        ws.receive_text()
        ws.send_text(json.dumps({"type": "ping"}))
        msg = ws.receive_text()
        data = json.loads(msg)
        assert data == {"type": "pong"}


def test_ws_files_file_event_flow(ws_client, tmp_path: Path):
    with ws_client.websocket_connect("/ws/files") as ws:
        # Drain snapshot
        ws.receive_text()
        # Create a file — event should arrive
        (tmp_path / "hello.md").write_text("# hi\n", encoding="utf-8")

        deadline_iters = 50
        seen = None
        for _ in range(deadline_iters):
            try:
                msg = ws.receive_text(timeout=1)
            except Exception:
                continue
            data = json.loads(msg)
            if data.get("type") == "file_event" and data.get("path") == "hello.md":
                seen = data
                break

        assert seen is not None, "did not receive file_event for hello.md"
        assert seen["event"] in ("created", "modified")
