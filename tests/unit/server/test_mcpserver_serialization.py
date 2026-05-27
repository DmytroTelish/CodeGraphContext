"""Unit tests for the optional tool-call serialization lock on MCPServer."""
import asyncio

import pytest

from codegraphcontext.server import MCPServer


@pytest.fixture
def isolated_server(tmp_path, monkeypatch):
    """Construct MCPServer with a throwaway Kuzu DB to avoid touching user data."""
    monkeypatch.setenv("CGC_RUNTIME_DB_TYPE", "kuzudb")
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "graph.kuzu"))
    loop = asyncio.new_event_loop()
    try:
        yield MCPServer(loop=loop, cwd=tmp_path)
    finally:
        loop.close()


def test_serialize_tool_calls_defaults_to_false(isolated_server):
    assert isolated_server._serialize_tool_calls is False
    assert isolated_server._tool_call_lock is None


def test_serialize_tool_calls_can_be_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CGC_RUNTIME_DB_TYPE", "kuzudb")
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "graph.kuzu"))
    monkeypatch.setenv("CGC_SERIALIZE_TOOL_CALLS", "1")
    loop = asyncio.new_event_loop()
    try:
        server = MCPServer(loop=loop, cwd=tmp_path)
        assert server._serialize_tool_calls is True
        assert isinstance(server._tool_call_lock, asyncio.Lock)
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_handle_tool_call_acquires_lock_when_enabled(tmp_path, monkeypatch):
    """When _tool_call_lock is set, handle_tool_call must acquire it before dispatch."""
    monkeypatch.setenv("CGC_RUNTIME_DB_TYPE", "kuzudb")
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "graph.kuzu"))
    monkeypatch.setenv("CGC_SERIALIZE_TOOL_CALLS", "1")

    loop = asyncio.get_running_loop()
    server = MCPServer(loop=loop, cwd=tmp_path)

    # Patch one tool handler to record whether the lock was held during invocation.
    held = {"value": False}

    def fake_handler(**_args):
        # If the lock is locked when we're inside the handler, serialization worked.
        held["value"] = server._tool_call_lock.locked()
        return {"ok": True}

    server.list_jobs_tool = fake_handler  # type: ignore[assignment]

    result = await server.handle_tool_call("list_jobs", {})
    assert result == {"ok": True}
    assert held["value"] is True
