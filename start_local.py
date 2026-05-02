#!/usr/bin/env python3
"""Nanobot 本地开发启动脚本（跨平台：macOS / Linux / Windows）。

一键启动所有本地开发服务：
  1. PostgreSQL (Docker 容器, 端口 5432)
  2. nanobot web 后端 (端口 18080)
  3. platform gateway (端口 8080)
  4. frontend dev server (端口 3080)

用法:
  # 启动所有服务
  python start_local.py

  # 仅启动部分服务
  python start_local.py --only db,gateway,frontend

  # 跳过某些服务
  python start_local.py --skip nanobot

  # 指定 nanobot config
  python start_local.py --nanobot-config ~/.nanobot/config.json

  # 停止所有服务
  python start_local.py --stop
"""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time

# ── 平台检测 ─────────────────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"

# ── 颜色输出 ──────────────────────────────────────────────────────────
GREEN = "\033[32m"
RED   = "\033[31m"
YELLOW = "\033[33m"
CYAN  = "\033[36m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RESET = "\033[0m"

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 服务配置 ──────────────────────────────────────────────────────────
SERVICES = {
    "db": {
        "name": "PostgreSQL",
        "port": 5432,
        "color": "\033[34m",
    },
    "nanobot": {
        "name": "Nanobot Web",
        "port": 18080,
        "color": "\033[35m",
    },
    "gateway": {
        "name": "Platform Gateway",
        "port": 8080,
        "color": "\033[36m",  # cyan
    },
    "frontend": {
        "name": "Frontend Dev",
        "port": 3080,
        "color": "\033[33m",  # yellow
    },
}


# ── 工具函数 ──────────────────────────────────────────────────────────

def log(msg: str, color: str = CYAN):
    print(f"{color}{BOLD}▸{RESET} {msg}")


def success(msg: str):
    print(f"{GREEN}✓{RESET} {msg}")


def error(msg: str):
    print(f"{RED}✗{RESET} {msg}")


def warn(msg: str):
    print(f"{YELLOW}⚠{RESET} {msg}")


def is_port_in_use(port: int) -> bool:
    """检查端口是否被占用。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, timeout: int = 30, name: str = "") -> bool:
    """等待端口可用。"""
    for i in range(timeout):
        if is_port_in_use(port):
            return True
        time.sleep(1)
        sys.stdout.write(f"\r  等待 {name or f'端口 {port}'}... ({i + 1}/{timeout}s)")
        sys.stdout.flush()
    print()
    return False


def _base_env(**extra) -> dict:
    """构建子进程环境变量，Windows 上额外注入 PYTHONIOENCODING=utf-8。"""
    env = {**os.environ}
    if IS_WINDOWS:
        env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.update(extra)
    return env


# ── PostgreSQL ────────────────────────────────────────────────────────

def start_postgres() -> bool:
    """启动 PostgreSQL Docker 容器。"""
    log("启动 PostgreSQL...")

    # 检查是否已有容器在运行
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", "name=^nanobot-local-postgres$"],
        capture_output=True, text=True,
    )
    if result.stdout.strip():
        success("PostgreSQL 已在运行")
        return True

    # 检查是否有已停止的容器
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=^nanobot-local-postgres$"],
        capture_output=True, text=True,
    )
    if result.stdout.strip():
        log("启动已有的 PostgreSQL 容器...")
        subprocess.run(["docker", "start", "nanobot-local-postgres"], check=True)
    else:
        log("创建新的 PostgreSQL 容器...")
        subprocess.run([
            "docker", "run", "-d",
            "--name", "nanobot-local-postgres",
            "-e", "POSTGRES_USER=nanobot",
            "-e", "POSTGRES_PASSWORD=nanobot",
            "-e", "POSTGRES_DB=nanobot_platform",
            "-v", "nanobot-local-pgdata:/var/lib/postgresql/data",
            "-p", "5432:5432",
            "postgres:16-alpine",
        ], check=True)

    if wait_for_port(5432, timeout=15, name="PostgreSQL"):
        success("PostgreSQL 就绪 (端口 5432)")
        return True
    else:
        error("PostgreSQL 启动超时")
        return False


def stop_postgres():
    """停止 PostgreSQL 容器。"""
    subprocess.run(["docker", "stop", "nanobot-local-postgres"], capture_output=True)
    success("PostgreSQL 已停止")


# ── Nanobot Web ───────────────────────────────────────────────────────

def start_nanobot_web(env: dict) -> "subprocess.Popen | None":
    log("启动 Nanobot Web 后端 (端口 18080)...")

    if is_port_in_use(18080):
        warn("端口 18080 已被占用，跳过 nanobot web")
        return None

    proc = subprocess.Popen(
        [sys.executable, "-m", "nanobot", "web", "--port", "18080", "--host", "0.0.0.0"],
        cwd=PROJECT_DIR,
        env=_base_env(**env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log(f"  PID: {proc.pid}")

    # 等待就绪再启动 gateway，避免 gateway 代理时返回 503
    if wait_for_port(18080, timeout=20, name="Nanobot Web"):
        success("Nanobot Web 就绪 (端口 18080)")
    else:
        warn("Nanobot Web 启动较慢，继续启动其他服务")

    return proc


# ── Platform Gateway ──────────────────────────────────────────────────

def start_gateway(env: dict) -> "subprocess.Popen | None":
    log("启动 Platform Gateway (端口 8080)...")

    if is_port_in_use(8080):
        warn("端口 8080 已被占用，跳过 gateway")
        return None

    proc_env = _base_env(
        PLATFORM_DATABASE_URL="postgresql+asyncpg://nanobot:nanobot@localhost:5432/nanobot_platform",
        # 本地开发模式：直接代理到本机 nanobot web，跳过 Docker 容器管理
        PLATFORM_DEV_NANOBOT_URL="http://127.0.0.1:18080",
        **env,
    )

    # 从项目根目录 .env 读取配置并注入 PLATFORM_ 前缀
    # 需要转发的变量：所有 *_API_KEY、*_API_BASE、JWT_SECRET、DEFAULT_MODEL
    _EXTRA_ENV_KEYS = {"JWT_SECRET", "DEFAULT_MODEL"}
    env_path = os.path.join(PROJECT_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip("'\"")
                    if key.endswith(("_API_KEY", "_API_BASE")) or key in _EXTRA_ENV_KEYS:
                        platform_key = f"PLATFORM_{key}"
                        proc_env.setdefault(platform_key, val)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", "8080", "--reload"],
        cwd=os.path.join(PROJECT_DIR, "platform"),
        env=proc_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log(f"  PID: {proc.pid}")
    return proc


# ── Frontend Dev Server ───────────────────────────────────────────────

def start_frontend() -> "subprocess.Popen | None":
    log("启动 Frontend Dev Server (端口 3080)...")

    if is_port_in_use(3080):
        warn("端口 3080 已被占用，跳过 frontend")
        return None

    frontend_dir = os.path.join(PROJECT_DIR, "frontend")

    if not os.path.exists(os.path.join(frontend_dir, "node_modules")):
        log("安装前端依赖...")
        # shell=True + 字符串命令在两个平台都能正确找到 npm / npm.cmd
        subprocess.run("npm install", cwd=frontend_dir, shell=True, check=True)

    proc = subprocess.Popen(
        "npm run dev",
        cwd=frontend_dir,
        env=_base_env(NEXT_PUBLIC_API_URL="http://127.0.0.1:8080"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
    )
    log(f"  PID: {proc.pid}")
    return proc


# ── 日志输出（跨平台：threading，不依赖 selectors/os.set_blocking）────

def tail_output(procs: dict):
    stop_event = threading.Event()

    def _reader(name: str, proc: "subprocess.Popen"):
        svc = SERVICES.get(name, {})
        color = svc.get("color", CYAN)
        try:
            for raw in iter(proc.stdout.readline, b""):
                if stop_event.is_set():
                    break
                text = raw.decode("utf-8", errors="replace").rstrip()
                if text:
                    print(f"{color}[{name:>8}]{RESET} {text}", flush=True)
        except (OSError, ValueError):
            pass

    threads = []
    for name, proc in procs.items():
        if proc and proc.stdout:
            t = threading.Thread(target=_reader, args=(name, proc), daemon=True)
            t.start()
            threads.append(t)

    try:
        while any(p.poll() is None for p in procs.values() if p):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=2)


# ── 停止所有服务 ──────────────────────────────────────────────────────

def stop_all():
    log("停止所有本地服务...")
    stop_postgres()

    if IS_WINDOWS:
        _stop_all_windows()
    else:
        _stop_all_unix()

    success("所有服务已停止")


def _stop_all_unix():
    patterns = ["nanobot web", "uvicorn app.main:app", "next dev.*3080"]
    for pattern in patterns:
        result = subprocess.run(
            f"pgrep -f '{pattern}'",
            shell=True, capture_output=True, text=True,
        )
        for pid in result.stdout.strip().split("\n"):
            pid = pid.strip()
            if pid.isdigit():
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    log(f"  终止进程 {pid} ({pattern})")
                except (ProcessLookupError, ValueError):
                    pass


def _stop_all_windows():
    # 进程名 → 用 tasklist 过滤
    image_names = ["nanobot.exe", "python.exe", "node.exe"]
    for image in image_names:
        try:
            result = subprocess.run(
                f'tasklist /FI "IMAGENAME eq {image}" /FO CSV /NH',
                shell=True, capture_output=True, text=True,
            )
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("INFO:") or "," not in line:
                    continue
                parts = line.split(",")
                if len(parts) >= 2:
                    pid = parts[1].strip('"').strip()
                    if pid.isdigit():
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                            log(f"  终止进程 {pid} ({image})")
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
        except Exception:
            pass


# ── 主入口 ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Nanobot 本地开发启动脚本")
    parser.add_argument("--stop", action="store_true", help="停止所有本地服务")
    parser.add_argument("--only", type=str, help="仅启动指定服务，逗号分隔 (db,nanobot,gateway,frontend)")
    parser.add_argument("--skip", type=str, help="跳过指定服务，逗号分隔")
    parser.add_argument("--no-tail", action="store_true", help="不跟踪日志输出")
    args = parser.parse_args()

    if args.stop:
        stop_all()
        return

    # 解析要启动的服务
    all_services = ["db", "nanobot", "gateway", "frontend"]
    enabled = [s.strip() for s in args.only.split(",")] if args.only else list(all_services)
    if args.skip:
        skip = {s.strip() for s in args.skip.split(",")}
        enabled = [s for s in enabled if s not in skip]

    platform_label = "Windows" if IS_WINDOWS else ("macOS" if sys.platform == "darwin" else "Linux")
    print(f"\n{BOLD}🔧 Nanobot 本地开发环境 ({platform_label}){RESET}\n")
    log(f"启动服务: {', '.join(enabled)}")

    processes: dict = {}
    extra_env: dict = {}

    try:
        # 1. PostgreSQL
        if "db" in enabled:
            result = subprocess.run("docker info", shell=True, capture_output=True)
            if result.returncode != 0:
                error("Docker 未运行，无法启动 PostgreSQL")
                error("请先启动 Docker，或使用 --skip db 跳过")
                sys.exit(1)
            if not start_postgres():
                sys.exit(1)

        # 2. Nanobot Web 后端（含就绪等待，gateway 代理依赖它）
        if "nanobot" in enabled:
            proc = start_nanobot_web(extra_env)
            if proc:
                processes["nanobot"] = proc

        # 3. Platform Gateway
        if "gateway" in enabled:
            proc = start_gateway(extra_env)
            if proc:
                processes["gateway"] = proc

        # 短暂等待 gateway 启动，frontend 依赖它
        if "gateway" in enabled and "frontend" in enabled:
            time.sleep(2)

        # 4. Frontend
        if "frontend" in enabled:
            proc = start_frontend()
            if proc:
                processes["frontend"] = proc

        if not processes:
            success("所有服务已就绪（使用已有实例）")
            return

        # 打印访问信息
        print(f"\n{BOLD}{'=' * 52}{RESET}")
        print(f"{BOLD}  本地开发环境已启动{RESET}")
        print(f"{'=' * 52}")
        for svc_id in enabled:
            svc = SERVICES[svc_id]
            if svc_id == "db":
                pid_info = "Docker 容器"
            elif svc_id in processes and processes[svc_id]:
                pid_info = f"PID {processes[svc_id].pid}"
            else:
                pid_info = "已有实例"
            print(f"  {svc['color']}{svc['name']:>20}{RESET}  http://127.0.0.1:{svc['port']}  ({pid_info})")
        print(f"{'=' * 52}")
        print(f"  {DIM}按 Ctrl+C 停止所有服务{RESET}\n")

        if not args.no_tail:
            tail_output(processes)
        else:
            # 等待所有进程
            for proc in processes.values():
                if proc:
                    proc.wait()

    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}正在停止服务...{RESET}")
    finally:
        # 清理进程
        for name, proc in processes.items():
            if proc and proc.poll() is None:
                log(f"停止 {name} (PID {proc.pid})...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

        # 如果启动了 db，也停止它
        if "db" in enabled:
            stop_postgres()

        success("所有服务已停止")


if __name__ == "__main__":
    main()
