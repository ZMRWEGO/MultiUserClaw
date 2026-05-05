"""Request routing — reverse-proxy from gateway to per-user nanobot containers.

Authenticated users' API requests (chat, sessions, WebSocket) are
forwarded to their individual Docker container.
"""

from __future__ import annotations

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config import settings
from app.container.manager import ensure_running
from app.db.engine import async_session, get_db
from app.db.models import User

router = APIRouter(prefix="/api/nanobot", tags=["proxy"])

# Response headers we transparently forward to the client when streaming.
# Must include Content-Length / Content-Type so browsers can render progress
# and pick the right blob handler; Content-Disposition gives the filename.
_PASSTHROUGH_HEADERS = {
    "content-type",
    "content-length",
    "content-disposition",
    "etag",
    "last-modified",
    "cache-control",
}


async def _container_url(db: AsyncSession, user: User) -> str:
    """Get the internal URL for the user's nanobot container, starting it if needed."""
    logger.info("[_container_url] dev_nanobot_url={}", settings.dev_nanobot_url)
    # Local dev mode: bypass Docker, forward to local nanobot web directly
    if settings.dev_nanobot_url:
        logger.info("[_container_url] local dev mode -> {}", settings.dev_nanobot_url)
        return settings.dev_nanobot_url
    logger.info("[_container_url] docker mode for user={}", user.id)
    container = await ensure_running(db, user.id)
    url = f"http://{container.internal_host}:{container.internal_port}"
    logger.info("[_container_url] docker container -> {}", url)
    return url


def _is_archive_download(path: str) -> bool:
    """Archive zip downloads can take long for big folders — drop the read timeout."""
    return path.startswith("workspace/archive/") and path.endswith("/download")


# ---------------------------------------------------------------------------
# HTTP reverse proxy  (catch-all for /api/nanobot/{path})
# ---------------------------------------------------------------------------

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_http(
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Forward HTTP requests to the user's nanobot container.

    JSON responses are deserialized and returned as a Python dict (existing
    behavior). All other content types are streamed back to the client in
    chunks via :class:`StreamingResponse`, preserving the upstream
    ``Content-Type`` / ``Content-Length`` / ``Content-Disposition`` headers
    so the browser sees a normal binary download.
    """
    logger.info("[proxy_http] path={} user={}", path, user.id)
    base_url = await _container_url(db, user)
    # Close the session explicitly so the connection returns to the pool
    # before the potentially long upstream call.
    await db.close()

    target_url = f"{base_url}/api/{path}"
    logger.info("[proxy_http] target_url={}", target_url)

    if request.query_params:
        target_url += f"?{request.query_params}"

    body = await request.body()

    # Long-running archive downloads must not be cut off by a read timeout.
    if _is_archive_download(path):
        timeout: httpx.Timeout | float = httpx.Timeout(
            connect=5.0, read=None, write=None, pool=5.0
        )
    else:
        timeout = 120.0

    client = httpx.AsyncClient(timeout=timeout)
    try:
        req = client.build_request(
            method=request.method,
            url=target_url,
            content=body,
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
        resp = await client.send(req, stream=True)
        logger.info(
            "[proxy_http] resp status={} ct={}",
            resp.status_code,
            resp.headers.get("content-type", ""),
        )
    except httpx.ConnectError as e:
        await client.aclose()
        logger.error("[proxy_http] ConnectError to {}: {}", target_url, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ConnectError to {target_url}. dev_url={settings.dev_nanobot_url!r}",
        )
    except Exception as e:
        await client.aclose()
        logger.error("[proxy_http] other error: {} {}", type(e).__name__, e)
        raise

    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        # JSON path: read fully, parse, close, return dict (FastAPI re-serializes).
        try:
            data = await resp.aread()
        finally:
            await resp.aclose()
            await client.aclose()
        import json as _json
        return _json.loads(data) if data else {}

    # Non-JSON: stream upstream chunks straight through. We keep ``resp`` and
    # ``client`` alive while the generator is active and close both in the
    # ``finally`` block to avoid leaks.
    async def _stream():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        _stream(),
        status_code=resp.status_code,
        media_type=ct or None,
        headers={
            k: v
            for k, v in resp.headers.items()
            if k.lower() in _PASSTHROUGH_HEADERS and k.lower() != "content-type"
        },
    )


# ---------------------------------------------------------------------------
# WebSocket reverse proxy
# ---------------------------------------------------------------------------

@router.websocket("/ws/{ws_path:path}")
async def proxy_websocket(
    websocket: WebSocket,
    ws_path: str,
    token: str = "",  # passed as query param ?token=xxx
):
    """Forward WebSocket connections to the user's nanobot container.

    Accepts arbitrary subpaths under /ws/ — supports both the chat WS
    (/ws/<session_id>) and file events WS (/ws/files), as well as future
    additions, without changing this proxy.
    """
    from app.auth.service import decode_token, get_user_by_id

    # Authenticate and resolve container URL, then release DB session immediately
    async with async_session() as db:
        payload = decode_token(token)
        if payload is None or payload.get("type") != "access":
            await websocket.close(code=4001, reason="Invalid token")
            return

        user = await get_user_by_id(db, payload["sub"])
        if user is None or not user.is_active:
            await websocket.close(code=4001, reason="User not found")
            return

        if settings.dev_nanobot_url:
            # Local dev mode: connect to local nanobot web directly
            target_ws_url = settings.dev_nanobot_url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws/{ws_path}"
        else:
            container = await ensure_running(db, user.id)
            target_ws_url = f"ws://{container.internal_host}:{container.internal_port}/ws/{ws_path}"
    # DB session is now released — not held during long-lived WebSocket relay

    await websocket.accept()

    import websockets

    try:
        async with websockets.connect(target_ws_url) as upstream:
            import asyncio

            async def client_to_upstream():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await upstream.send(data)
                except WebSocketDisconnect:
                    pass

            async def upstream_to_client():
                try:
                    async for message in upstream:
                        await websocket.send_text(message)
                except websockets.ConnectionClosed:
                    pass

            # When one side closes, cancel the other so we don't hang
            tasks = [asyncio.create_task(client_to_upstream()),
                     asyncio.create_task(upstream_to_client())]
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
