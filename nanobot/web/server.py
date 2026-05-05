"""FastAPI web server for nanobot frontend."""

from __future__ import annotations

import asyncio
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, TYPE_CHECKING

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from nanobot.config.loader import load_config, get_config_path, get_data_dir
from nanobot.config.schema import Config
from nanobot.bus.queue import MessageBus
from nanobot.session.manager import SessionManager
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule, CronJob
from nanobot.providers.registry import PROVIDERS

if TYPE_CHECKING:
    from nanobot.channels.web import WebChannel


# ============================================================================
# Request/Response models
# ============================================================================


class ChatRequest(BaseModel):
    message: str
    session_id: str = "web:default"
    attachments: list[dict[str, str]] | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str


class AddCronJobRequest(BaseModel):
    name: str
    message: str
    every_seconds: int | None = None
    cron_expr: str | None = None
    at_iso: str | None = None
    deliver: bool = False
    channel: str | None = None
    to: str | None = None


class ToggleCronJobRequest(BaseModel):
    enabled: bool


class AddMarketplaceRequest(BaseModel):
    source: str


class CreateArchiveRequest(BaseModel):
    path: str


# ============================================================================
# App factory
# ============================================================================


def create_app(
    *,
    bus: MessageBus | None = None,
    web_channel: "WebChannel | None" = None,
    session_manager: SessionManager | None = None,
    config: Config | None = None,
    cron_service: CronService | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Two modes:
    - **Gateway mode** (bus + web_channel provided): messages go through the
      MessageBus; the WebChannel's ``_handle_message`` publishes inbound
      messages and the AgentLoop processes them asynchronously.
    - **Standalone mode** (no bus): creates its own AgentLoop and uses
      ``process_direct()`` for synchronous request-response (legacy).
    """
    if config is None:
        config = load_config()

    app = FastAPI(title="nanobot", version="0.1.0")

    # CORS for frontend dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Standalone fallback: create an isolated AgentLoop when no bus provided
    if bus is None:
        from nanobot.agent.loop import AgentLoop
        from nanobot.providers.litellm_provider import LiteLLMProvider

        bus = MessageBus()
        provider = _make_provider(config)
        session_manager = SessionManager(config.workspace_path)
        cron_store_path = get_data_dir() / "cron" / "jobs.json"
        cron_service = CronService(cron_store_path)

        agent = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=config.agents.defaults.model,
            max_iterations=config.agents.defaults.max_tool_iterations,
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            cron_service=cron_service,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=session_manager,
        )
        app.state.agent = agent
    else:
        app.state.agent = None  # gateway mode – no standalone agent

    if session_manager is None:
        session_manager = SessionManager(config.workspace_path)
    if cron_service is None:
        cron_store_path = get_data_dir() / "cron" / "jobs.json"
        cron_service = CronService(cron_store_path)

    app.state.config = config
    app.state.session_manager = session_manager
    app.state.cron_service = cron_service
    app.state.bus = bus
    app.state.web_channel = web_channel  # may be None in standalone

    # Lazy-built workspace watcher (Phase 2). Created on first /ws/files connect.
    # WebChannel.start() may pre-set a watcher; otherwise we build one on demand.
    if not hasattr(app.state, "workspace_watcher"):
        app.state.workspace_watcher = None

    # Purge any leftover archive temp dirs from a previous (possibly crashed)
    # process before we start serving requests.
    from nanobot.web import archive as _archive_module

    _archive_module.purge_residue(config.workspace_path)

    _register_routes(app)
    return app


def _make_provider(config: Config):
    """Create LLM provider from config."""
    from nanobot.providers.litellm_provider import LiteLLMProvider

    model = config.agents.defaults.model

    if config.is_proxy_mode:
        return LiteLLMProvider(
            default_model=model,
            proxy_url=config.proxy.url,
            proxy_token=config.proxy.token,
        )

    p = config.get_provider()
    if not (p and p.api_key) and not model.startswith("bedrock/"):
        raise RuntimeError("No API key configured. Set one in ~/.nanobot/config.json")
    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=config.get_provider_name(),
    )


# ============================================================================
# Routes
# ============================================================================


def _register_routes(app: FastAPI) -> None:
    """Register all API routes."""

    # ------ Chat ------

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        """Send a message.

        Gateway mode: publishes to the bus and returns immediately.
        Standalone mode: processes synchronously and returns the response.
        """
        session_key = req.session_id
        chat_id = session_key.split(":", 1)[-1] if ":" in session_key else session_key

        # Resolve attachment file paths
        media_paths: list[str] = []
        if req.attachments:
            from nanobot.web.files import get_file_path
            config_ref: Config = app.state.config
            for att in req.attachments:
                fpath = get_file_path(config_ref.workspace_path, att.get("file_id", ""))
                if fpath:
                    media_paths.append(str(fpath))

        web_channel: "WebChannel | None" = app.state.web_channel

        if web_channel is not None:
            # Gateway mode – async via bus
            await web_channel._handle_message(
                sender_id="web_user",
                chat_id=chat_id,
                content=req.message,
                media=media_paths or None,
                metadata={"attachments": req.attachments} if req.attachments else None,
            )
            # Notify connected clients that processing started
            await web_channel.notify_thinking(chat_id)
            return {"status": "accepted", "session_id": session_key}
        else:
            # Standalone fallback
            from nanobot.agent.loop import AgentLoop

            agent: AgentLoop = app.state.agent
            response = await agent.process_direct(
                content=req.message,
                session_key=session_key,
                channel="web",
                chat_id=chat_id,
            )
            return ChatResponse(response=response, session_id=session_key)

    @app.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest):
        """Send a message and stream the response via SSE (standalone mode only)."""
        from nanobot.agent.loop import AgentLoop

        agent: AgentLoop | None = app.state.agent
        if agent is None:
            raise HTTPException(
                status_code=400,
                detail="Streaming not available in gateway mode. Use WebSocket.",
            )

        session_key = req.session_id

        async def event_generator():
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            try:
                response = await agent.process_direct(
                    content=req.message,
                    session_key=session_key,
                    channel="web",
                    chat_id=session_key.split(":", 1)[-1] if ":" in session_key else session_key,
                )
                chunk_size = 20
                for i in range(0, len(response), chunk_size):
                    chunk = response[i : i + chunk_size]
                    yield f"data: {json.dumps({'type': 'content', 'content': chunk})}\n\n"
                    await asyncio.sleep(0.02)
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # ------ WebSocket ------

    @app.websocket("/ws/files")
    async def workspace_files_ws(websocket: WebSocket):
        """Push workspace file events to the client (Phase 2).

        Client may send `{"type":"ping"}`; server replies `{"type":"pong"}`.
        Server sends `{"type":"snapshot"}` once on connect, then `file_event`
        payloads for each created/modified/deleted/moved event.
        """
        from nanobot.web.watcher import WorkspaceWatcher

        await websocket.accept()
        logger.debug("workspace files WebSocket connected")

        watcher: WorkspaceWatcher | None = getattr(app.state, "workspace_watcher", None)
        if watcher is None:
            # Lazy-construct per-app watcher (standalone / non-WebChannel mode)
            config_ref: Config = app.state.config
            watcher = WorkspaceWatcher(config_ref.workspace_path)
            app.state.workspace_watcher = watcher

        queue = watcher.add_listener()

        # Initial snapshot — tell client to do a fresh browse for state alignment
        try:
            await websocket.send_text(json.dumps({"type": "snapshot"}))
        except Exception:
            watcher.remove_listener(queue)
            return

        try:
            while True:
                # Concurrently wait for an event from queue OR a client message (ping)
                queue_task = asyncio.create_task(queue.get())
                recv_task = asyncio.create_task(websocket.receive_text())
                done, pending = await asyncio.wait(
                    [queue_task, recv_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Cancel pending tasks to avoid leaks
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                for t in done:
                    if t is queue_task:
                        try:
                            event = t.result()
                            await websocket.send_text(json.dumps(event))
                        except Exception:
                            pass
                    elif t is recv_task:
                        try:
                            raw = t.result()
                        except Exception:
                            raise WebSocketDisconnect()
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if data.get("type") == "ping":
                            await websocket.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            logger.debug("workspace files WebSocket disconnected")
        except Exception as e:
            logger.error(f"workspace files WebSocket error: {e}")
        finally:
            watcher.remove_listener(queue)

    @app.websocket("/ws/{session_id}")
    async def websocket_endpoint(websocket: WebSocket, session_id: str):
        """WebSocket endpoint for real-time chat.

        Clients send: {"type":"message","content":"..."}
        Server sends: {"type":"message","role":"assistant","content":"..."}
                      {"type":"status","status":"thinking"}
        """
        web_channel: "WebChannel | None" = app.state.web_channel

        await websocket.accept()

        if web_channel is not None:
            web_channel.register_connection(session_id, websocket)

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if data.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                    continue

                if data.get("type") == "message":
                    content = data.get("content", "").strip()
                    if not content:
                        continue

                    # Extract file attachments if present
                    attachments = data.get("attachments") or []
                    media_paths: list[str] = []
                    if attachments:
                        from nanobot.web.files import get_file_path
                        config_ref: Config = app.state.config
                        for att in attachments:
                            fpath = get_file_path(config_ref.workspace_path, att.get("file_id", ""))
                            if fpath:
                                media_paths.append(str(fpath))

                    if web_channel is not None:
                        # Gateway mode – publish via bus
                        await web_channel._handle_message(
                            sender_id="web_user",
                            chat_id=session_id,
                            content=content,
                            media=media_paths or None,
                            metadata={"attachments": attachments} if attachments else None,
                        )
                        await web_channel.notify_thinking(session_id)
                    else:
                        # Standalone fallback – process directly
                        from nanobot.agent.loop import AgentLoop

                        agent: AgentLoop = app.state.agent
                        session_key = f"web:{session_id}"
                        response = await agent.process_direct(
                            content=content,
                            session_key=session_key,
                            channel="web",
                            chat_id=session_id,
                        )
                        await websocket.send_text(json.dumps({
                            "type": "message",
                            "role": "assistant",
                            "content": response,
                        }))

        except WebSocketDisconnect:
            logger.debug(f"WebSocket disconnected for session {session_id}")
        except Exception as e:
            logger.error(f"WebSocket error for session {session_id}: {e}")
        finally:
            if web_channel is not None:
                web_channel.unregister_connection(session_id, websocket)
        """Push workspace file events to the client (Phase 2).

        Client may send `{"type":"ping"}`; server replies `{"type":"pong"}`.
        Server sends `{"type":"snapshot"}` once on connect, then `file_event`
        payloads for each created/modified/deleted/moved event.
        """
        from nanobot.web.watcher import WorkspaceWatcher

        await websocket.accept()

        watcher: WorkspaceWatcher | None = getattr(app.state, "workspace_watcher", None)
        if watcher is None:
            # Lazy-construct per-app watcher (standalone / non-WebChannel mode)
            config_ref: Config = app.state.config
            watcher = WorkspaceWatcher(config_ref.workspace_path)
            app.state.workspace_watcher = watcher

        queue = watcher.add_listener()

        # Initial snapshot — tell client to do a fresh browse for state alignment
        try:
            await websocket.send_text(json.dumps({"type": "snapshot"}))
        except Exception:
            watcher.remove_listener(queue)
            return

        try:
            while True:
                # Concurrently wait for an event from queue OR a client message (ping)
                queue_task = asyncio.create_task(queue.get())
                recv_task = asyncio.create_task(websocket.receive_text())
                done, pending = await asyncio.wait(
                    [queue_task, recv_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Cancel pending tasks to avoid leaks
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                for t in done:
                    if t is queue_task:
                        try:
                            event = t.result()
                            await websocket.send_text(json.dumps(event))
                        except Exception:
                            pass
                    elif t is recv_task:
                        try:
                            raw = t.result()
                        except Exception:
                            raise WebSocketDisconnect()
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if data.get("type") == "ping":
                            await websocket.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            logger.debug("workspace files WebSocket disconnected")
        except Exception as e:
            logger.error(f"workspace files WebSocket error: {e}")
        finally:
            watcher.remove_listener(queue)

    # ------ Sessions ------

    @app.get("/api/sessions")
    async def list_sessions():
        """List all conversation sessions."""
        sm: SessionManager = app.state.session_manager
        return sm.list_sessions()

    @app.get("/api/sessions/{key:path}")
    async def get_session(key: str):
        """Get a session's message history."""
        sm: SessionManager = app.state.session_manager
        session = sm.get_or_create(key)
        # Filter out tool messages and assistant messages with tool_calls
        # (intermediate steps), only keep user messages and final assistant replies
        visible_messages = []
        for m in session.messages:
            role = m.get("role", "")
            # Skip tool result messages (e.g. SKILL.md content, file reads, etc.)
            if role == "tool":
                continue
            # Skip assistant messages that are just tool call requests (not final replies)
            if role == "assistant" and m.get("tool_calls"):
                continue
            msg_data: dict[str, Any] = {
                "role": role,
                "content": m.get("content", ""),
                "timestamp": m.get("timestamp"),
            }
            # Include attachments if stored in metadata
            meta = m.get("metadata")
            if isinstance(meta, dict):
                attachments = meta.get("attachments")
                if attachments:
                    msg_data["attachments"] = attachments
            visible_messages.append(msg_data)

        return {
            "key": session.key,
            "messages": visible_messages,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }

    @app.delete("/api/sessions/{key:path}")
    async def delete_session(key: str):
        """Delete a session."""
        sm: SessionManager = app.state.session_manager
        if sm.delete(key):
            return {"ok": True}
        raise HTTPException(status_code=404, detail="Session not found")

    # ------ Status ------

    @app.get("/api/status")
    async def get_status():
        """Get system status."""
        config: Config = app.state.config
        config_path = get_config_path()

        providers_status = []
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_local:
                providers_status.append({
                    "name": spec.label,
                    "has_key": bool(p.api_base),
                    "detail": p.api_base or "",
                })
            else:
                providers_status.append({
                    "name": spec.label,
                    "has_key": bool(p.api_key),
                })

        channels_status = []
        for ch_name in ["whatsapp", "telegram", "discord", "feishu", "dingtalk", "email", "slack", "qq", "web"]:
            ch_cfg = getattr(config.channels, ch_name, None)
            if ch_cfg:
                channels_status.append({
                    "name": ch_name,
                    "enabled": getattr(ch_cfg, "enabled", False),
                })

        cron: CronService = app.state.cron_service
        cron_status = cron.status()

        return {
            "config_path": str(config_path),
            "config_exists": config_path.exists(),
            "workspace": str(config.workspace_path),
            "workspace_exists": config.workspace_path.exists(),
            "model": config.agents.defaults.model,
            "max_tokens": config.agents.defaults.max_tokens,
            "temperature": config.agents.defaults.temperature,
            "max_tool_iterations": config.agents.defaults.max_tool_iterations,
            "providers": providers_status,
            "channels": channels_status,
            "cron": cron_status,
        }

    # ------ Cron Jobs ------

    @app.get("/api/cron/jobs")
    async def list_cron_jobs(include_disabled: bool = False):
        """List cron jobs."""
        cron: CronService = app.state.cron_service
        jobs = cron.list_jobs(include_disabled=include_disabled)
        return [_serialize_job(j) for j in jobs]

    @app.post("/api/cron/jobs")
    async def add_cron_job(req: AddCronJobRequest):
        """Add a new cron job."""
        cron: CronService = app.state.cron_service

        if req.every_seconds:
            schedule = CronSchedule(kind="every", every_ms=req.every_seconds * 1000)
        elif req.cron_expr:
            schedule = CronSchedule(kind="cron", expr=req.cron_expr)
        elif req.at_iso:
            import datetime
            dt = datetime.datetime.fromisoformat(req.at_iso)
            schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
        else:
            raise HTTPException(status_code=400, detail="Must specify every_seconds, cron_expr, or at_iso")

        job = cron.add_job(
            name=req.name,
            schedule=schedule,
            message=req.message,
            deliver=req.deliver,
            channel=req.channel,
            to=req.to,
        )
        return _serialize_job(job)

    @app.delete("/api/cron/jobs/{job_id}")
    async def remove_cron_job(job_id: str):
        """Remove a cron job."""
        cron: CronService = app.state.cron_service
        if cron.remove_job(job_id):
            return {"ok": True}
        raise HTTPException(status_code=404, detail="Job not found")

    @app.put("/api/cron/jobs/{job_id}/toggle")
    async def toggle_cron_job(job_id: str, req: ToggleCronJobRequest):
        """Enable or disable a cron job."""
        cron: CronService = app.state.cron_service
        job = cron.enable_job(job_id, enabled=req.enabled)
        if job:
            return _serialize_job(job)
        raise HTTPException(status_code=404, detail="Job not found")

    @app.post("/api/cron/jobs/{job_id}/run")
    async def run_cron_job(job_id: str):
        """Manually run a cron job."""
        cron: CronService = app.state.cron_service
        if await cron.run_job(job_id, force=True):
            return {"ok": True}
        raise HTTPException(status_code=404, detail="Job not found")

    # ------ Skills ------

    @app.get("/api/skills")
    async def list_skills():
        """List all skills (builtin + workspace)."""
        from nanobot.agent.skills import SkillsLoader

        config: Config = app.state.config
        loader = SkillsLoader(config.workspace_path)
        raw = loader.list_skills(filter_unavailable=False)
        result = []
        for s in raw:
            meta = loader.get_skill_metadata(s["name"]) or {}
            available = loader._check_requirements(loader._get_skill_meta(s["name"]))
            result.append({
                "name": s["name"],
                "description": meta.get("description", s["name"]),
                "source": s["source"],
                "available": available,
                "path": s["path"],
            })
        return result

    @app.delete("/api/skills/{name}")
    async def delete_skill(name: str):
        """Delete a workspace skill."""
        from nanobot.agent.skills import SkillsLoader

        config: Config = app.state.config
        loader = SkillsLoader(config.workspace_path)

        # Check the skill exists and is a workspace skill
        all_skills = loader.list_skills(filter_unavailable=False)
        skill = next((s for s in all_skills if s["name"] == name), None)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        if skill["source"] != "workspace":
            raise HTTPException(status_code=400, detail="Cannot delete builtin skills")

        skill_dir = loader.workspace_skills / name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        return {"ok": True}

    @app.get("/api/skills/{name}/download")
    async def download_skill(name: str):
        """Download a skill as a zip file."""
        from nanobot.agent.skills import SkillsLoader
        import io

        config: Config = app.state.config
        loader = SkillsLoader(config.workspace_path)

        all_skills = loader.list_skills(filter_unavailable=False)
        skill = next((s for s in all_skills if s["name"] == name), None)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")

        # Resolve the skill directory from the SKILL.md path
        skill_dir = Path(skill["path"]).parent

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in skill_dir.rglob("*"):
                if file_path.is_file():
                    arcname = f"{name}/{file_path.relative_to(skill_dir)}"
                    zf.write(file_path, arcname)
        from fastapi.responses import Response
        from nanobot.web.files import content_disposition
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": content_disposition("attachment", f"{name}.zip")},
        )

    @app.post("/api/skills/upload")
    async def upload_skill(file: UploadFile = File(...)):
        """Upload a skill as a zip file."""
        from nanobot.agent.skills import SkillsLoader

        config: Config = app.state.config
        loader = SkillsLoader(config.workspace_path)

        if not file.filename or not file.filename.endswith(".zip"):
            raise HTTPException(status_code=400, detail="File must be a .zip archive")

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            with zipfile.ZipFile(tmp_path, "r") as zf:
                names = zf.namelist()
                # Find SKILL.md — could be at root or inside a single top-level dir
                skill_md_entries = [n for n in names if n.endswith("SKILL.md")]
                if not skill_md_entries:
                    raise HTTPException(status_code=400, detail="Zip must contain a SKILL.md file")

                # Determine skill name and extraction
                skill_md = skill_md_entries[0]
                parts = skill_md.split("/")
                if len(parts) == 1:
                    # SKILL.md at root — use zip filename as skill name
                    skill_name = file.filename.rsplit(".", 1)[0]
                else:
                    # SKILL.md inside a directory — use that directory name
                    skill_name = parts[0]

                target_dir = loader.workspace_skills / skill_name
                target_dir.mkdir(parents=True, exist_ok=True)

                if len(parts) == 1:
                    # Extract everything to skill_name/
                    zf.extractall(target_dir)
                else:
                    # Extract contents of the top-level dir
                    prefix = skill_name + "/"
                    for member in names:
                        if member.startswith(prefix):
                            rel = member[len(prefix):]
                            if not rel:
                                continue
                            dest = target_dir / rel
                            if member.endswith("/"):
                                dest.mkdir(parents=True, exist_ok=True)
                            else:
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                with zf.open(member) as src, open(dest, "wb") as dst:
                                    dst.write(src.read())
        finally:
            tmp_path.unlink(missing_ok=True)

        # Return the newly created skill info
        meta = loader.get_skill_metadata(skill_name) or {}
        available = loader._check_requirements(loader._get_skill_meta(skill_name))
        return {
            "name": skill_name,
            "description": meta.get("description", skill_name),
            "source": "workspace",
            "available": available,
            "path": str(loader.workspace_skills / skill_name / "SKILL.md"),
        }

    # ------ Files ------

    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

    @app.post("/api/files/upload")
    async def upload_file(
        file: UploadFile = File(...),
        session_id: str = Form("web:default"),
    ):
        """Upload a file for chat attachment or analysis."""
        from nanobot.web.files import save_file, generate_file_id

        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")

        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large (max 50MB)")

        file_id = generate_file_id()
        ct = file.content_type or "application/octet-stream"
        config: Config = app.state.config
        metadata = save_file(
            workspace=config.workspace_path,
            file_id=file_id,
            filename=file.filename,
            content=content,
            content_type=ct,
            session_id=session_id,
        )
        metadata["url"] = f"/api/files/{file_id}"
        return metadata

    @app.get("/api/files")
    async def list_uploaded_files(session_id: str | None = None):
        """List uploaded files, optionally filtered by session."""
        from nanobot.web.files import list_files

        config: Config = app.state.config
        return list_files(config.workspace_path, session_id=session_id)

    @app.get("/api/files/{file_id}")
    async def download_file(file_id: str):
        """Download a file by ID."""
        from nanobot.web.files import get_file_metadata, get_file_path

        config: Config = app.state.config
        meta = get_file_metadata(config.workspace_path, file_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="File not found")

        file_path = get_file_path(config.workspace_path, file_id)
        if file_path is None:
            raise HTTPException(status_code=404, detail="File data missing")

        ct = meta.get("content_type", "application/octet-stream")
        disposition = "inline" if ct.startswith("image/") else "attachment"
        filename = meta["name"]

        from fastapi.responses import Response
        from nanobot.web.files import content_disposition
        return Response(
            content=file_path.read_bytes(),
            media_type=ct,
            headers={"Content-Disposition": content_disposition(disposition, filename)},
        )

    @app.delete("/api/files/{file_id}")
    async def remove_file(file_id: str):
        """Delete a file."""
        from nanobot.web.files import delete_file

        config: Config = app.state.config
        if delete_file(config.workspace_path, file_id):
            return {"ok": True}
        raise HTTPException(status_code=404, detail="File not found")

    # ------ Workspace Browser ------

    @app.get("/api/workspace/browse")
    async def browse_workspace_dir(path: str = ""):
        """Browse workspace directory contents."""
        from nanobot.web.files import browse_workspace

        config: Config = app.state.config
        try:
            return browse_workspace(config.workspace_path, path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/workspace/download")
    async def download_workspace_file(path: str):
        """Download a single workspace file by relative path.

        Directories are no longer zipped here — use POST /api/workspace/archive
        instead so the work happens off the request thread and the gateway can
        stream the result.
        """
        from nanobot.web.files import _resolve_workspace_path, content_disposition

        config: Config = app.state.config
        target = _resolve_workspace_path(config.workspace_path, path)
        if target is None or not target.exists():
            raise HTTPException(status_code=404, detail="Path not found")

        if target.is_dir():
            raise HTTPException(
                status_code=400,
                detail="Use POST /api/workspace/archive for directories",
            )

        import mimetypes
        from fastapi.responses import Response

        ct, _ = mimetypes.guess_type(target.name)
        ct = ct or "application/octet-stream"
        disposition = "inline" if ct.startswith("image/") else "attachment"
        return Response(
            content=target.read_bytes(),
            media_type=ct,
            headers={"Content-Disposition": content_disposition(disposition, target.name)},
        )

    # ------ Workspace folder archive (zip job) ------

    @app.post("/api/workspace/archive", status_code=201)
    async def create_workspace_archive(body: CreateArchiveRequest):
        """Create an asynchronous folder-archive job."""
        from nanobot.web import archive as _archive

        config: Config = app.state.config
        try:
            job = _archive.create_job(config.workspace_path, body.path)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        await _archive.start_job(config.workspace_path, job)
        return {"job_id": job.job_id, "status": job.status}

    @app.get("/api/workspace/archive/{job_id}")
    async def get_workspace_archive_status(job_id: str):
        """Get the status of an archive job."""
        from nanobot.web import archive as _archive

        job = _archive.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "status": job.status,
            "bytes_written": job.bytes_written,
            "total_bytes_estimate": job.total_bytes_estimate,
            "error": job.error,
        }

    @app.get("/api/workspace/archive/{job_id}/download")
    async def download_workspace_archive(job_id: str):
        """Stream the completed zip; clean up the job temp dir afterwards."""
        from fastapi.responses import FileResponse
        from starlette.background import BackgroundTask

        from nanobot.web import archive as _archive
        from nanobot.web.files import content_disposition

        job = _archive.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "ready" or job.zip_path is None:
            raise HTTPException(status_code=409, detail="Archive not ready")

        zip_name = job.zip_path.name
        return FileResponse(
            job.zip_path,
            media_type="application/zip",
            filename=zip_name,
            headers={"Content-Disposition": content_disposition("attachment", zip_name)},
            background=BackgroundTask(_archive.delete_job, job_id),
        )

    @app.get("/api/workspace/preview")
    async def preview_workspace_file(path: str):
        """Return a structured preview payload for a workspace file.

        Response shape varies by ``kind``; see docs/tech-spec-workspace-file-preview.md.
        Text-based kinds (text/markdown/json) embed content (with 5MB cap).
        Image/PDF/HTML/docx/xlsx return only ``download_url`` and metadata.
        Binary returns ``reason`` (office_legacy | archive | unknown) plus download_url.
        """
        from datetime import datetime, timezone
        import mimetypes as _mimetypes

        from nanobot.web.files import _resolve_workspace_path
        from nanobot.web.preview import (
            TEXT_PREVIEW_LIMIT,
            _binary_reason_for,
            preview_kind_for,
            pretty_print_json,
            read_text_safely,
        )

        config: Config = app.state.config
        target = _resolve_workspace_path(config.workspace_path, path)
        if target is None:
            raise HTTPException(status_code=400, detail="Invalid path")
        if not target.exists():
            raise HTTPException(status_code=404, detail="File not found")
        if not target.is_file():
            raise HTTPException(status_code=400, detail="Not a file")

        stat = target.stat()
        size = stat.st_size
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        download_url = f"/api/workspace/download?path={target.relative_to(config.workspace_path.resolve())}"

        kind, language = preview_kind_for(target)
        ext = target.suffix.lower()

        if kind == "text":
            content, truncated = read_text_safely(target, TEXT_PREVIEW_LIMIT)
            return {
                "kind": "text",
                "language": language or "plain",
                "content": content,
                "size": size,
                "truncated": truncated,
                "modified": modified,
            }

        if kind == "markdown":
            content, truncated = read_text_safely(target, TEXT_PREVIEW_LIMIT)
            return {
                "kind": "markdown",
                "content": content,
                "size": size,
                "truncated": truncated,
                "modified": modified,
            }

        if kind == "json":
            # Too-large JSON: degrade to binary (downloadable)
            if size > TEXT_PREVIEW_LIMIT:
                return {
                    "kind": "binary",
                    "reason": "unknown",
                    "size": size,
                    "modified": modified,
                    "download_url": download_url,
                }
            content, _ = read_text_safely(target, TEXT_PREVIEW_LIMIT)
            return {
                "kind": "json",
                "content": pretty_print_json(content),
                "size": size,
                "modified": modified,
            }

        if kind == "image":
            ct, _ct_enc = _mimetypes.guess_type(target.name)
            return {
                "kind": "image",
                "download_url": download_url,
                "content_type": ct or "application/octet-stream",
                "size": size,
                "modified": modified,
            }

        if kind == "pdf":
            return {
                "kind": "pdf",
                "download_url": download_url,
                "size": size,
                "modified": modified,
            }

        if kind == "html":
            return {
                "kind": "html",
                "download_url": download_url,
                "size": size,
                "modified": modified,
            }

        if kind == "docx":
            return {
                "kind": "docx",
                "download_url": download_url,
                "size": size,
                "modified": modified,
            }

        if kind == "xlsx":
            return {
                "kind": "xlsx",
                "download_url": download_url,
                "size": size,
                "modified": modified,
            }

        # binary fallback
        return {
            "kind": "binary",
            "reason": _binary_reason_for(ext),
            "size": size,
            "modified": modified,
            "download_url": download_url,
        }

    @app.get("/api/workspace/files")
    async def search_workspace_files_endpoint(q: str = "", limit: int = 20):
        """Search workspace for preview-able files (used by chat input @-mention).

        Returns top-N candidates ordered by `(rank asc, mtime desc)` where rank
        favors name-prefix > name-substring > path-prefix > path-substring > fuzzy.
        Excludes binary files, directories, hidden entries, and noise dirs
        (.git/node_modules/__pycache__/.venv/venv/dist/build/.next).
        """
        from nanobot.web.files import search_workspace_files

        config: Config = app.state.config
        return search_workspace_files(config.workspace_path, q, limit)

    @app.post("/api/workspace/upload")
    async def upload_to_workspace(
        file: UploadFile = File(...),
        path: str = Form(""),
    ):
        """Upload a file to a specific workspace directory."""
        from nanobot.web.files import save_to_workspace

        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large (max 50MB)")
        config: Config = app.state.config
        try:
            return save_to_workspace(config.workspace_path, path, file.filename, content)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/workspace/delete")
    async def delete_workspace_item(path: str):
        """Delete a file or directory from workspace."""
        from nanobot.web.files import delete_workspace_path

        config: Config = app.state.config
        if delete_workspace_path(config.workspace_path, path):
            return {"ok": True}
        raise HTTPException(status_code=404, detail="Path not found")

    @app.post("/api/workspace/mkdir")
    async def create_workspace_directory(path: str):
        """Create a directory in workspace."""
        from nanobot.web.files import create_workspace_dir

        config: Config = app.state.config
        try:
            return create_workspace_dir(config.workspace_path, path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ------ Plugins ------

    @app.get("/api/plugins")
    async def list_plugins():
        """List all loaded plugins with their agents, commands, and skills."""
        from nanobot.agent.plugins import PluginLoader

        config: Config = app.state.config
        loader = PluginLoader(config.workspace_path)

        result = []
        for plugin in loader.plugins.values():
            result.append({
                "name": plugin.name,
                "description": plugin.description,
                "source": plugin.source,
                "agents": [
                    {
                        "name": a.name,
                        "description": a.description,
                        "model": a.model,
                    }
                    for a in plugin.agents.values()
                ],
                "commands": [
                    {
                        "name": c.name,
                        "description": c.description,
                        "argument_hint": c.argument_hint,
                    }
                    for c in plugin.commands.values()
                ],
                "skills": [
                    skill_dir.name
                    for skill_dir_root in plugin.skill_dirs
                    for skill_dir in sorted(skill_dir_root.iterdir())
                    if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists()
                ],
            })
        return result

    # ------ Commands (plugin slash commands) ------

    @app.get("/api/commands")
    async def list_commands():
        """List all available slash commands (built-ins + plugin commands)."""
        from nanobot.agent.plugins import PluginLoader

        config: Config = app.state.config
        loader = PluginLoader(config.workspace_path)

        commands = [
            {"name": "new", "description": "Start a new conversation", "argument_hint": None, "plugin_name": "builtin"},
            {"name": "help", "description": "Show available commands", "argument_hint": None, "plugin_name": "builtin"},
        ]
        for plugin in loader.plugins.values():
            for cmd in plugin.commands.values():
                commands.append({
                    "name": cmd.name,
                    "description": cmd.description,
                    "argument_hint": cmd.argument_hint,
                    "plugin_name": cmd.plugin_name,
                })

        # Add skills (skip name collisions with plugin commands)
        from nanobot.agent.skills import SkillsLoader

        plugin_cmd_names = {cmd.name for p in loader.plugins.values() for cmd in p.commands.values()}
        extra_dirs = loader.get_skill_dirs()
        skills_loader = SkillsLoader(config.workspace_path, extra_dirs=extra_dirs)
        for s in skills_loader.list_skills(filter_unavailable=True):
            if s["name"] not in plugin_cmd_names and s["name"] not in {"new", "help"}:
                commands.append({
                    "name": s["name"],
                    "description": skills_loader._get_skill_description(s["name"]),
                    "argument_hint": None,
                    "plugin_name": "skill",
                })

        return commands

    # ------ Marketplace ------

    @app.get("/api/marketplaces")
    async def list_marketplaces():
        """List all registered marketplaces."""
        from nanobot.agent.marketplace import MarketplaceManager
        mgr = MarketplaceManager()
        return [
            {"name": m.name, "source": m.source, "type": m.type}
            for m in mgr.list_marketplaces()
        ]

    @app.post("/api/marketplaces")
    async def add_marketplace(req: AddMarketplaceRequest):
        """Register a new marketplace from local path or Git URL."""
        from nanobot.agent.marketplace import MarketplaceManager
        mgr = MarketplaceManager()
        try:
            entry = mgr.add_marketplace(req.source)
            return {"name": entry.name, "source": entry.source, "type": entry.type}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/marketplaces/{name}")
    async def remove_marketplace(name: str):
        """Remove a registered marketplace."""
        from nanobot.agent.marketplace import MarketplaceManager
        mgr = MarketplaceManager()
        try:
            mgr.remove_marketplace(name)
            return {"ok": True}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/marketplaces/{name}/update")
    async def update_marketplace(name: str):
        """Update (clone or pull) a marketplace's cached data."""
        from nanobot.agent.marketplace import MarketplaceManager
        mgr = MarketplaceManager()
        try:
            entry = mgr.update_marketplace(name)
            return {"name": entry.name, "source": entry.source, "type": entry.type}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/marketplaces/{name}/plugins")
    async def list_marketplace_plugins(name: str):
        """List available plugins in a marketplace."""
        from nanobot.agent.marketplace import MarketplaceManager
        mgr = MarketplaceManager()
        try:
            plugins = mgr.list_available_plugins(name)
            return [
                {
                    "name": p.name,
                    "description": p.description,
                    "marketplace_name": p.marketplace_name,
                    "installed": p.installed,
                }
                for p in plugins
            ]
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/marketplaces/{name}/plugins/{plugin_name}/install")
    async def install_marketplace_plugin(name: str, plugin_name: str):
        """Install a plugin from a marketplace."""
        from nanobot.agent.marketplace import MarketplaceManager
        mgr = MarketplaceManager()
        try:
            dest = mgr.install_plugin(name, plugin_name)
            return {"ok": True, "path": str(dest)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/plugins/{plugin_name}")
    async def uninstall_plugin(plugin_name: str):
        """Uninstall a plugin."""
        from nanobot.agent.marketplace import MarketplaceManager
        mgr = MarketplaceManager()
        try:
            mgr.uninstall_plugin(plugin_name)
            return {"ok": True}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------ Health ------

    @app.get("/api/ping")
    async def ping():
        return {"message": "pong"}


def _serialize_job(job: CronJob) -> dict[str, Any]:
    """Serialize a CronJob to a JSON-friendly dict."""
    sched_str = ""
    if job.schedule.kind == "every":
        secs = (job.schedule.every_ms or 0) // 1000
        if secs >= 3600:
            sched_str = f"every {secs // 3600}h"
        elif secs >= 60:
            sched_str = f"every {secs // 60}m"
        else:
            sched_str = f"every {secs}s"
    elif job.schedule.kind == "cron":
        sched_str = job.schedule.expr or ""
    else:
        sched_str = "one-time"

    next_run = None
    if job.state.next_run_at_ms:
        next_run = job.state.next_run_at_ms

    last_run = None
    if job.state.last_run_at_ms:
        last_run = job.state.last_run_at_ms

    return {
        "id": job.id,
        "name": job.name,
        "enabled": job.enabled,
        "schedule_kind": job.schedule.kind,
        "schedule_display": sched_str,
        "schedule_expr": job.schedule.expr,
        "schedule_every_ms": job.schedule.every_ms,
        "message": job.payload.message,
        "deliver": job.payload.deliver,
        "channel": job.payload.channel,
        "to": job.payload.to,
        "next_run_at_ms": next_run,
        "last_run_at_ms": last_run,
        "last_status": job.state.last_status,
        "last_error": job.state.last_error,
        "created_at_ms": job.created_at_ms,
    }
