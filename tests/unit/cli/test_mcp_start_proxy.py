"""Unit tests for cgc mcp start --daemon-socket proxy mode."""
from unittest.mock import patch

from typer.testing import CliRunner

from codegraphcontext.cli.main import app


def test_mcp_start_with_daemon_socket_invokes_proxy():
    runner = CliRunner()
    with patch("codegraphcontext.cli.main.run_stdio_proxy") as mock_proxy, \
         patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.cli.main.asyncio.new_event_loop") as mock_loop:
        result = runner.invoke(
            app,
            ["mcp", "start", "--daemon-socket", "/tmp/proxy.sock"],
        )
        assert result.exit_code == 0
        # run_stdio_proxy must have been called exactly once with the given socket,
        # auto_start=True, and a log_path keyword
        mock_proxy.assert_called_once()
        call_args = mock_proxy.call_args
        assert call_args.args[0] == "/tmp/proxy.sock"
        assert call_args.kwargs.get("auto_start") is True
        assert "log_path" in call_args.kwargs
        # In proxy mode, the legacy in-process MCPServer event loop must NOT be created
        mock_loop.assert_not_called()


def test_mcp_start_with_no_daemon_falls_back_to_in_process():
    runner = CliRunner()
    with patch("codegraphcontext.cli.main.run_stdio_proxy") as mock_proxy, \
         patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.cli.main.MCPServer") as mock_server_cls, \
         patch("codegraphcontext.cli.main.asyncio.new_event_loop") as mock_loop, \
         patch("codegraphcontext.cli.main.asyncio.set_event_loop"):
        # Pretend the loop drives the coroutine; we only need to verify which branch ran.
        mock_loop.return_value.run_until_complete.return_value = None
        result = runner.invoke(app, ["mcp", "start", "--no-daemon"])
        assert result.exit_code == 0
        mock_proxy.assert_not_called()
        # Fall-through must have created an MCPServer
        mock_server_cls.assert_called_once()
