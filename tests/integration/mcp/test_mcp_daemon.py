import asyncio
import json
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codegraphcontext.mcp_daemon import run_mcp_daemon


@pytest.fixture
def short_socket_dir():
    """Per-test Unix socket dir rooted in /tmp.

    AF_UNIX paths are limited to ~104 bytes on macOS / Linux; pytest's
    tmp_path resolves under /var/folders/.../pytest-of-USER/... which
    blows that limit. Root the temp dir in /tmp instead so the socket
    path stays short.
    """
    path = Path(tempfile.mkdtemp(prefix="cgc-d-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_mcp_daemon_serves_jsonrpc_over_unix_socket(short_socket_dir):
    socket_path = short_socket_dir / "daemon.sock"
    fake_server = MagicMock()
    fake_server.code_watcher = MagicMock()
    process_mock = MagicMock(
        side_effect=lambda request: {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"echo": request["method"]},
        }
    )

    async def fake_process(request):
        return process_mock(request)

    fake_server.process_jsonrpc_request = fake_process

    with patch("codegraphcontext.mcp_daemon.MCPServer", return_value=fake_server):
        task = asyncio.create_task(run_mcp_daemon(str(socket_path)))
        try:
            for _ in range(50):
                if socket_path.exists():
                    break
                await asyncio.sleep(0.02)
            assert socket_path.exists()

            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            writer.write(
                b'{"jsonrpc":"2.0","id":7,"method":"initialize","params":{}}\n'
            )
            await writer.drain()

            response_line = await asyncio.wait_for(reader.readline(), timeout=1)
            response = json.loads(response_line.decode())
            assert response == {
                "jsonrpc": "2.0",
                "id": 7,
                "result": {"echo": "initialize"},
            }

            writer.close()
            await writer.wait_closed()
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            socket_path.unlink(missing_ok=True)

    fake_server.code_watcher.start.assert_called_once()
    fake_server.shutdown.assert_called_once()
    process_mock.assert_called_once()
