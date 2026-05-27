"""Unit tests for cgc mcp daemon CLI command."""
from unittest.mock import patch

from typer.testing import CliRunner

from codegraphcontext.cli.main import app


def test_cgc_mcp_daemon_help_lists_socket_path_option():
    # NO_COLOR + TERM=dumb disables rich's ANSI escape codes, which otherwise
    # split flag names like `--socket-path` across escape sequences in CI's narrow terminal.
    runner = CliRunner()
    result = runner.invoke(
        app, ["mcp", "daemon", "--help"], env={"NO_COLOR": "1", "TERM": "dumb"}
    )
    assert result.exit_code == 0
    assert "--socket-path" in result.output


def test_cgc_mcp_daemon_invokes_run_mcp_daemon():
    runner = CliRunner()
    with patch("codegraphcontext.cli.main.run_mcp_daemon") as mock_run, \
         patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.cli.main.asyncio.run") as mock_async_run:
        result = runner.invoke(app, ["mcp", "daemon", "--socket-path", "/tmp/x.sock"])
        assert result.exit_code == 0
        mock_async_run.assert_called_once()
        mock_run.assert_called_once_with("/tmp/x.sock")
