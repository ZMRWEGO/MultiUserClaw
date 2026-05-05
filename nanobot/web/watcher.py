"""Workspace file change watcher.

Bridges watchdog (a sync, threaded library) into asyncio so that file events
can fan out to one queue per WebSocket subscriber. Lazy-starts the underlying
Observer when the first listener subscribes; stops it when the last listener
unsubscribes — saves background IO when no clients are watching.

Events are debounced over a short window (default 100ms) so that a single
"save" doesn't spam multiple `modified` events.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

# watchdog imports are deferred to .start() so importing this module never fails
# even when watchdog is unavailable (it is in pyproject.toml; this is a guard
# for environments that intentionally run without it).


# Directory names to skip recursively (mostly noisy / build / vendored).
_SKIP_DIRS: set[str] = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".nuxt",
    ".vercel",
    "node_modules",
    "__pycache__",
    ".venv",
    ".env",
    "venv",
    "env",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".idea",
    ".vscode",
    ".DS_Store",
}


# Default debounce window in seconds.
DEBOUNCE_SECONDS = 0.1


class WorkspaceWatcher:
    """Watches a workspace directory and fans out file events to multiple async queues.

    Usage:
        watcher = WorkspaceWatcher(workspace)
        queue = watcher.add_listener()  # auto-starts observer on first listener
        try:
            while True:
                event = await queue.get()
                ...
        finally:
            watcher.remove_listener(queue)  # auto-stops observer on last listener
    """

    def __init__(self, workspace: Path, *, debounce_seconds: float = DEBOUNCE_SECONDS) -> None:
        self._workspace = workspace.resolve()
        self._debounce_seconds = debounce_seconds
        self._observer: Any = None  # watchdog.observers.Observer
        self._listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        self._listeners_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Pending debounced events: rel_path -> (event_kind, scheduled_at_loop_time)
        # Stored as (event_dict, asyncio.TimerHandle)
        self._pending: dict[str, tuple[dict[str, Any], asyncio.TimerHandle]] = {}
        self._pending_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_listener(self) -> asyncio.Queue[dict[str, Any]]:
        """Add a new listener. Lazily starts the underlying observer."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._listeners_lock:
            self._listeners.add(queue)
            should_start = self._observer is None
        if should_start:
            self._start()
        return queue

    def remove_listener(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a listener. Stops the observer when the last one disconnects."""
        with self._listeners_lock:
            self._listeners.discard(queue)
            should_stop = not self._listeners
        if should_stop:
            self._stop()

    def stop(self) -> None:
        """Force-stop the observer. Safe to call multiple times."""
        with self._listeners_lock:
            self._listeners.clear()
        self._stop()

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def listener_count(self) -> int:
        with self._listeners_lock:
            return len(self._listeners)

    # ------------------------------------------------------------------
    # Internal: observer lifecycle
    # ------------------------------------------------------------------

    def _start(self) -> None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler  # noqa: F401
        except ImportError as e:
            logger.warning(f"watchdog not available; file events disabled: {e}")
            return

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("WorkspaceWatcher.start() called without a running event loop")
            return

        if not self._workspace.exists():
            logger.warning(
                f"WorkspaceWatcher: workspace path does not exist: {self._workspace}"
            )
            return

        try:
            observer = Observer()
            handler = _Handler(self)
            observer.schedule(handler, str(self._workspace), recursive=True)
            observer.start()
            self._observer = observer
            logger.info(f"WorkspaceWatcher started for {self._workspace}")
        except Exception as e:
            logger.warning(f"WorkspaceWatcher failed to start: {e}")
            self._observer = None

    def _stop(self) -> None:
        observer = self._observer
        self._observer = None
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=2.0)
                logger.info("WorkspaceWatcher stopped")
            except Exception as e:
                logger.warning(f"WorkspaceWatcher stop error: {e}")

        # Cancel any pending debounced events
        with self._pending_lock:
            for _, handle in self._pending.values():
                try:
                    handle.cancel()
                except Exception:
                    pass
            self._pending.clear()

    # ------------------------------------------------------------------
    # Internal: event filtering + debounce + fanout
    # ------------------------------------------------------------------

    def _is_under_skipped_dir(self, abs_path: Path) -> bool:
        """Check if any path component is a skipped directory name."""
        try:
            rel = abs_path.relative_to(self._workspace)
        except ValueError:
            return True
        for part in rel.parts:
            if part in _SKIP_DIRS:
                return True
            # Also skip dotfiles/dirs (consistent with browse_workspace)
            if part.startswith("."):
                return True
        return False

    def _to_relative(self, abs_path: str) -> str | None:
        """Convert absolute path string to workspace-relative; return None if outside."""
        try:
            return str(Path(abs_path).resolve().relative_to(self._workspace))
        except (ValueError, OSError):
            return None

    def _build_event(
        self,
        kind: str,
        src_path: str,
        is_directory: bool,
        dest_path: str | None = None,
    ) -> dict[str, Any] | None:
        """Build a serializable event dict, filtering out skipped/external paths."""
        src = Path(src_path)
        if self._is_under_skipped_dir(src):
            return None

        rel = self._to_relative(src_path)
        if rel is None:
            return None

        payload: dict[str, Any] = {
            "type": "file_event",
            "event": kind,
            "path": rel,
            "is_directory": is_directory,
        }

        if dest_path is not None:
            dest = Path(dest_path)
            if self._is_under_skipped_dir(dest):
                # Move into skipped dir → treat as deleted
                payload["event"] = "deleted"
            else:
                rel_dest = self._to_relative(dest_path)
                if rel_dest is not None:
                    payload["old_path"] = rel
                    payload["path"] = rel_dest

        # Try to attach size + modified mtime (best-effort)
        target_path = Path(payload.get("path", rel))
        try:
            stat = (self._workspace / target_path).stat()
            if not is_directory:
                payload["size"] = stat.st_size
            payload["modified"] = datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat()
        except OSError:
            # File may have been deleted by the time we stat it
            pass

        return payload

    def _on_raw_event(
        self,
        kind: str,
        src_path: str,
        is_directory: bool,
        dest_path: str | None = None,
    ) -> None:
        """Called from watchdog's thread. Schedule debounced delivery via the loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        payload = self._build_event(kind, src_path, is_directory, dest_path)
        if payload is None:
            return

        # Use the *final* path key for debounce dedup
        dedup_key = f"{payload['event']}:{payload['path']}"

        def schedule_in_loop() -> None:
            with self._pending_lock:
                # Cancel any earlier pending event for the same key
                existing = self._pending.get(dedup_key)
                if existing is not None:
                    try:
                        existing[1].cancel()
                    except Exception:
                        pass

                handle = loop.call_later(
                    self._debounce_seconds,
                    self._flush_pending,
                    dedup_key,
                )
                self._pending[dedup_key] = (payload, handle)

        try:
            loop.call_soon_threadsafe(schedule_in_loop)
        except RuntimeError:
            # Loop already closed
            pass

    def _flush_pending(self, dedup_key: str) -> None:
        """Called on the main event loop after debounce window expires."""
        with self._pending_lock:
            entry = self._pending.pop(dedup_key, None)
        if entry is None:
            return
        payload, _handle = entry
        # Snapshot listeners under lock, then push outside (so put_nowait can't deadlock)
        with self._listeners_lock:
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the event for this listener; they're behind. Should not normally happen.
                pass


class _Handler:
    """Adapts watchdog's FileSystemEventHandler protocol onto WorkspaceWatcher."""

    def __init__(self, watcher: WorkspaceWatcher):
        self._watcher = watcher

    def dispatch(self, event: Any) -> None:
        # watchdog calls dispatch on every event; we emit one of created/modified/deleted/moved.
        et = event.event_type  # 'created' | 'modified' | 'deleted' | 'moved'
        if et not in ("created", "modified", "deleted", "moved"):
            return
        is_dir = bool(getattr(event, "is_directory", False))
        src_path = getattr(event, "src_path", "")
        dest_path = getattr(event, "dest_path", None) if et == "moved" else None
        # Some platforms emit "modified" on directories on every contained file change;
        # filter those out, they're noisy and not actionable for the UI.
        if et == "modified" and is_dir:
            return
        self._watcher._on_raw_event(
            kind=et, src_path=src_path, is_directory=is_dir, dest_path=dest_path
        )
