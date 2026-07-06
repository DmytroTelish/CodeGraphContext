"""Unit tests for the extracted process_jsonrpc_request method."""
import asyncio

import pytest

from codegraphcontext.cli import config_manager
from codegraphcontext.server import MCPServer


@pytest.fixture
def server(tmp_path, monkeypatch):
    # Isolate from this machine's real ~/.codegraphcontext/config.yaml: a
    # "named" mode with a default_context pointing at a remote-only backend
    # would otherwise override CGC_RUNTIME_DB_TYPE below and fail on a
    # missing FALKORDB_HOST.
    monkeypatch.setattr(config_manager, "CONTEXT_CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(config_manager, "_LEGACY_CONTEXT_CONFIG_FILE", tmp_path / "cgc_config.yaml")
    monkeypatch.setenv("CGC_RUNTIME_DB_TYPE", "kuzudb")
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "graph.kuzu"))
    loop = asyncio.new_event_loop()
    try:
        yield MCPServer(loop=loop, cwd=tmp_path)
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_process_jsonrpc_initialize(server):
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    response = await server.process_jsonrpc_request(request)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "CodeGraphContext"
    assert "protocolVersion" in response["result"]


@pytest.mark.asyncio
async def test_process_jsonrpc_tools_list(server):
    request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    response = await server.process_jsonrpc_request(request)
    assert isinstance(response["result"]["tools"], list)
    assert len(response["result"]["tools"]) > 0


@pytest.mark.asyncio
async def test_process_jsonrpc_unknown_method(server):
    request = {"jsonrpc": "2.0", "id": 3, "method": "does_not_exist", "params": {}}
    response = await server.process_jsonrpc_request(request)
    assert response["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_process_jsonrpc_initialized_notification_returns_none(server):
    request = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    response = await server.process_jsonrpc_request(request)
    assert response is None


@pytest.mark.asyncio
async def test_process_jsonrpc_tools_call_happy_path(server, monkeypatch):
    """tools/call routes to handle_tool_call and wraps result as MCP content."""
    async def fake_handle(_name, _args):
        return {"data": "result-payload"}
    monkeypatch.setattr(server, "handle_tool_call", fake_handle)
    request = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {"name": "list_jobs", "arguments": {}},
    }
    response = await server.process_jsonrpc_request(request)
    assert response["id"] == 10
    assert "result" in response
    assert response["result"]["content"][0]["type"] == "text"
    assert "result-payload" in response["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_process_jsonrpc_tools_call_error_returns_minus_32000(server, monkeypatch):
    """When handle_tool_call returns {error: ...}, response carries -32000 with the error data."""
    async def fake_handle(_name, _args):
        return {"error": "Unknown tool: foo"}
    monkeypatch.setattr(server, "handle_tool_call", fake_handle)
    request = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {"name": "foo", "arguments": {}},
    }
    response = await server.process_jsonrpc_request(request)
    assert response["error"]["code"] == -32000


@pytest.mark.asyncio
async def test_process_jsonrpc_inner_exception_returns_minus_32603(server, monkeypatch):
    """Handler raising inside dispatch must be caught and converted to -32603."""
    async def fake_handle(_name, _args):
        raise RuntimeError("simulated handler failure")
    monkeypatch.setattr(server, "handle_tool_call", fake_handle)
    request = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {"name": "list_jobs", "arguments": {}},
    }
    response = await server.process_jsonrpc_request(request)
    assert response["error"]["code"] == -32603
    assert "simulated handler failure" in response["error"]["message"]
