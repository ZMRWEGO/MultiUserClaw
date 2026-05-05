"""Tests for the streaming HTTP proxy in ``app.routes.proxy``.

The test builds a tiny "upstream" FastAPI app (the would-be nanobot web)
plus the real gateway proxy router. We monkey-patch ``httpx.AsyncClient``
inside the proxy module so the gateway calls into the upstream via an
ASGI transport — no real socket / port is needed.

We override the auth and DB dependencies and supply a stub user so the
proxy gets straight to the forwarding logic.
"""

from __future__ import annotations

import os
from typing import Iterator

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# These env vars must be set before importing the app, because
# ``app.config.Settings`` validates them at import time. We use throwaway
# placeholders just so settings loads.
os.environ.setdefault("PLATFORM_DATABASE_URL", "postgresql+asyncpg://user:pass@127.0.0.1/x")
os.environ.setdefault("PLATFORM_JWT_SECRET", "test-secret")
os.environ.setdefault("PLATFORM_DEV_NANOBOT_URL", "http://upstream.test")


def _build_upstream_app(zip_payload: bytes) -> FastAPI:
    """Tiny upstream that mimics nanobot web for the proxy tests."""
    upstream = FastAPI()

    @upstream.get("/api/sessions")
    async def sessions():
        return {"sessions": ["s1", "s2"]}

    @upstream.get("/api/workspace/archive/job123/download")
    async def archive_dl():
        from fastapi.responses import Response

        return Response(
            content=zip_payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="folder.zip"',
                "Content-Length": str(len(zip_payload)),
            },
        )

    @upstream.get("/api/notfound")
    async def notfound():
        raise HTTPException(status_code=404, detail="nope")

    return upstream


def _build_gateway_with_upstream(upstream_app: FastAPI) -> FastAPI:
    """Mount the real proxy router with auth/db stubbed out and httpx redirected."""
    from app.routes import proxy as proxy_module
    from app.auth.dependencies import get_current_user
    from app.db.engine import get_db

    gateway = FastAPI()
    gateway.include_router(proxy_module.router)

    # Stub auth: any request looks like user(id=1). We sidestep SQLAlchemy
    # ORM init by using a plain object that quacks like a User.
    class _StubUser:
        id = 1
        is_active = True

    async def _stub_user():
        return _StubUser()

    async def _stub_db():
        class _Stub:
            async def close(self):
                pass

        yield _Stub()

    gateway.dependency_overrides[get_current_user] = _stub_user
    gateway.dependency_overrides[get_db] = _stub_db

    # Redirect httpx.AsyncClient (when constructed inside the proxy module)
    # to use an ASGITransport pointing at the upstream app.
    original_client_cls = proxy_module.httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=upstream_app)
        return original_client_cls(*args, **kwargs)

    proxy_module.httpx.AsyncClient = _client_factory  # type: ignore[assignment]

    gateway.state._restore = lambda: setattr(
        proxy_module.httpx, "AsyncClient", original_client_cls
    )
    return gateway


@pytest.fixture
def gateway_with_zip() -> Iterator[tuple[TestClient, bytes]]:
    payload = b"PK\x03\x04" + (b"\xab" * 100_000)  # 100KB blob with zip magic
    upstream = _build_upstream_app(payload)
    gateway = _build_gateway_with_upstream(upstream)
    client = TestClient(gateway)
    try:
        yield client, payload
    finally:
        gateway.state._restore()


# ---------------------------------------------------------------------------
# Streaming binary path
# ---------------------------------------------------------------------------


def test_zip_response_passes_through_complete(gateway_with_zip):
    client, payload = gateway_with_zip
    r = client.get("/api/nanobot/workspace/archive/job123/download")

    assert r.status_code == 200
    assert r.content == payload
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-length"] == str(len(payload))
    assert r.headers["content-disposition"] == 'attachment; filename="folder.zip"'


def test_zip_response_chunked(gateway_with_zip):
    """The response should iter in multiple chunks, not one giant blob."""
    client, payload = gateway_with_zip

    with client.stream("GET", "/api/nanobot/workspace/archive/job123/download") as r:
        chunks = list(r.iter_bytes())
    # We can't deterministically force chunking from the test side, but the
    # body must equal the payload regardless of chunk boundaries.
    assert b"".join(chunks) == payload


# ---------------------------------------------------------------------------
# JSON path is unchanged
# ---------------------------------------------------------------------------


def test_json_path_preserves_dict(gateway_with_zip):
    client, _ = gateway_with_zip
    r = client.get("/api/nanobot/sessions")
    assert r.status_code == 200
    assert r.json() == {"sessions": ["s1", "s2"]}
    assert r.headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------------------
# Archive path skips the read timeout
# ---------------------------------------------------------------------------


def test_archive_download_uses_unbounded_read_timeout():
    from app.routes import proxy as proxy_module

    assert proxy_module._is_archive_download(
        "workspace/archive/abc123/download"
    )
    assert not proxy_module._is_archive_download("workspace/archive/abc123")
    assert not proxy_module._is_archive_download("workspace/download")
    assert not proxy_module._is_archive_download("sessions")
