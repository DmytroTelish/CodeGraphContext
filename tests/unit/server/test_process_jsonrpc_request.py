"""Unit tests for the extracted process_jsonrpc_request method."""
import asyncio

import pytest

from codegraphcontext.server import MCPServer


@pytest.fixture
def server(tmp_path, monkeypatch):
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
