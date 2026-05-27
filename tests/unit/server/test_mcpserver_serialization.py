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
    lock_states = []

    def fake_handler(**_args):
        # If the lock is locked when we're inside the handler, serialization worked.
        lock_states.append(server._tool_call_lock.locked())
        return {"ok": True}

    server.list_jobs_tool = fake_handler  # type: ignore[assignment]

    result = await server.handle_tool_call("list_jobs", {})
    assert result == {"ok": True}
    assert lock_states == [True]


@pytest.mark.asyncio
async def test_handle_tool_call_truly_serializes_concurrent_calls(tmp_path, monkeypatch):
    """Two concurrent handle_tool_call invocations must execute serially."""
    monkeypatch.setenv("CGC_RUNTIME_DB_TYPE", "kuzudb")
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "graph.kuzu"))
    monkeypatch.setenv("CGC_SERIALIZE_TOOL_CALLS", "1")

    loop = asyncio.get_running_loop()
    server = MCPServer(loop=loop, cwd=tmp_path)

    events = []

    def slow_handler(*, call_id):
        import time
        events.append(("enter", call_id))
        time.sleep(0.05)
        events.append(("exit", call_id))
        return {"call_id": call_id}

    server.list_jobs_tool = slow_handler  # type: ignore[assignment]

    results = await asyncio.gather(
        server.handle_tool_call("list_jobs", {"call_id": 1}),
        server.handle_tool_call("list_jobs", {"call_id": 2}),
    )

    assert {r["call_id"] for r in results} == {1, 2}
    # Serialized: events look like [(enter, X), (exit, X), (enter, Y), (exit, Y)]
    assert len(events) == 4
    first_call = events[0][1]
    assert events[1] == ("exit", first_call), f"Expected exit of {first_call} before any other enter, got {events}"
    second_call = events[2][1]
    assert events[3] == ("exit", second_call), f"Expected clean serial ordering, got {events}"
    assert first_call != second_call
