import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from codegraphcontext.server import MCPServer
from codegraphcontext.utils.debug_log import info_logger, warning_logger


DEFAULT_DAEMON_SOCKET_PATH = str(Path.home() / ".codegraphcontext" / "mcp-daemon.sock")
DEFAULT_DAEMON_LOG_PATH = str(Path.home() / ".codegraphcontext" / "mcp-daemon.log")
DEFAULT_DAEMON_STARTUP_TIMEOUT_SEC = 10.0


def daemon_socket_is_ready(socket_path: str) -> bool:
    """Return True when a daemon is actively accepting connections on the socket."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(0.2)
    try:
        client.connect(socket_path)
        return True
    except OSError:
        return False
    finally:
        client.close()


def _prepare_daemon_socket(socket_path: Path) -> None:
    """Create the socket parent directory and clear any stale socket file."""
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if not socket_path.exists():
        return

    if daemon_socket_is_ready(str(socket_path)):
        raise RuntimeError(f"CodeGraphContext MCP daemon is already running at {socket_path}")

    warning_logger(f"Removing stale MCP daemon socket at {socket_path}")
    socket_path.unlink(missing_ok=True)


async def run_mcp_daemon(socket_path: str):
    """Run a long-lived MCP daemon that serves JSON-RPC over a Unix socket."""
    socket_path_obj = Path(socket_path).expanduser().resolve()
    _prepare_daemon_socket(socket_path_obj)

    loop = asyncio.get_running_loop()
    server = MCPServer(loop=loop, cwd=Path.cwd())
    server.code_watcher.start()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                payload = line.decode().strip()
                if not payload:
                    continue

                try:
                    request = json.loads(payload)
                except json.JSONDecodeError as exc:
                    response = {
                        "jsonrpc": "2.0",
                        "id": "unknown",
                        "error": {
                            "code": -32700,
                            "message": f"Parse error: {exc}",
                        },
                    }
                else:
                    response = await server.process_jsonrpc_request(request)

                if response is not None:
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    unix_server = await asyncio.start_unix_server(handle_client, path=str(socket_path_obj))
    info_logger(f"CodeGraphContext MCP daemon listening on {socket_path_obj}")
    try:
        async with unix_server:
            await unix_server.serve_forever()
    finally:
        server.shutdown()
        socket_path_obj.unlink(missing_ok=True)


def _spawn_daemon_process(socket_path: str, log_path: Optional[str] = None) -> None:
    """Start the daemon in the background using the current Python executable."""
    log_path = log_path or DEFAULT_DAEMON_LOG_PATH
    log_path_obj = Path(log_path).expanduser().resolve()
    log_path_obj.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "codegraphcontext.cli.main",
        "mcp",
        "daemon",
        "--socket-path",
        socket_path,
    ]
    with open(log_path_obj, "ab") as log_file:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            close_fds=True,
            cwd=os.getcwd(),
            env=os.environ.copy(),
        )


def ensure_daemon_running(
    socket_path: str,
    *,
    log_path: Optional[str] = None,
    startup_timeout_sec: float = DEFAULT_DAEMON_STARTUP_TIMEOUT_SEC,
) -> None:
    """Start the daemon if needed and wait until it begins accepting connections."""
    if daemon_socket_is_ready(socket_path):
        return

    _spawn_daemon_process(socket_path, log_path=log_path)
    deadline = time.time() + startup_timeout_sec
    while time.time() < deadline:
        if daemon_socket_is_ready(socket_path):
            return
        time.sleep(0.1)

    raise RuntimeError(f"Timed out waiting for CodeGraphContext MCP daemon at {socket_path}")


def run_stdio_proxy(
    socket_path: str,
    *,
    auto_start: bool = False,
    log_path: Optional[str] = None,
) -> None:
    """Forward stdio JSON-RPC traffic to a shared local MCP daemon."""
    if auto_start:
        ensure_daemon_running(socket_path, log_path=log_path)
    elif not daemon_socket_is_ready(socket_path):
        raise RuntimeError(f"CodeGraphContext MCP daemon is not running at {socket_path}")

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(socket_path)

    def pump_stdin():
        try:
            for line in sys.stdin.buffer:
                client.sendall(line)
        finally:
            try:
                client.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    stdin_thread = threading.Thread(target=pump_stdin, daemon=True)
    stdin_thread.start()

    try:
        client_file = client.makefile("rb")
        for line in client_file:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
    finally:
        stdin_thread.join(timeout=1)
        client.close()
