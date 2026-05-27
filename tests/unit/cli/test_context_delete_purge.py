"""`cgc context delete` auto-purges graph for fork-created contexts.

Behavior verified:
- Fork-created context delete issues GRAPH.DELETE for its graph_name.
- Non-fork context delete leaves the graph untouched (existing behavior).
- --keep-graph flag suppresses the purge even for fork-created contexts.
- If the GRAPH.DELETE fails, the registry entry is still removed (we'd
  rather lose track of an orphan than be stuck in a half-deleted state).
"""
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from codegraphcontext.cli import config_manager
from codegraphcontext.cli.main import app


def _redirect_config_dir(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".codegraphcontext"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_manager, "CONTEXT_CONFIG_FILE", cfg_dir / "config.yaml")
    monkeypatch.setattr(
        config_manager, "_LEGACY_CONTEXT_CONFIG_FILE", cfg_dir / "cgc_config.yaml"
    )
    monkeypatch.setattr(config_manager, "_LEGACY_FALKORDB_PATH", cfg_dir / "global" / "falkordb.db")
    return cfg_dir


def _fake_db_manager_with_redis():
    fake_redis = MagicMock()
    fake_falkordb = MagicMock()
    fake_falkordb.connection = fake_redis
    fake_manager = MagicMock()
    fake_manager._driver = fake_falkordb
    fake_manager.get_driver.return_value = MagicMock()
    return fake_manager, fake_redis


def test_delete_purges_graph_for_fork_created_context(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    config_manager.create_context(
        "botty-feature-X",
        database="falkordb-remote",
        graph_name="botty-feature-X-graph",
        repo_path="/Users/dev/feat",
        created_via_fork=True,
    )

    fake_manager, fake_redis = _fake_db_manager_with_redis()

    runner = CliRunner()
    with patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.core.get_database_manager", return_value=fake_manager):
        result = runner.invoke(app, ["context", "delete", "botty-feature-X"], input="y\n")

    assert result.exit_code == 0, result.output
    # GRAPH.DELETE issued for the cloned graph
    delete_calls = [
        call for call in fake_redis.execute_command.call_args_list
        if call.args and call.args[0] == "GRAPH.DELETE"
    ]
    assert len(delete_calls) == 1
    assert delete_calls[0].args[1] == "botty-feature-X-graph"

    # Registry entry removed
    assert "botty-feature-X" not in config_manager.load_context_config().contexts


def test_delete_does_not_purge_graph_for_non_fork_context(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    config_manager.create_context(
        "botty-stage",
        database="falkordb-remote",
        graph_name="botty-stage-graph",
        repo_path="/Users/dev/stage",
        # Note: created_via_fork left at default False
    )

    fake_manager, fake_redis = _fake_db_manager_with_redis()

    runner = CliRunner()
    with patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.core.get_database_manager", return_value=fake_manager):
        result = runner.invoke(app, ["context", "delete", "botty-stage"], input="y\n")

    assert result.exit_code == 0, result.output
    # No GRAPH.DELETE
    delete_calls = [
        call for call in fake_redis.execute_command.call_args_list
        if call.args and call.args[0] == "GRAPH.DELETE"
    ]
    assert len(delete_calls) == 0
    # Registry entry still removed
    assert "botty-stage" not in config_manager.load_context_config().contexts


def test_delete_with_keep_graph_skips_purge_even_for_fork(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    config_manager.create_context(
        "botty-feature-X",
        database="falkordb-remote",
        graph_name="botty-feature-X-graph",
        repo_path="/Users/dev/feat",
        created_via_fork=True,
    )

    fake_manager, fake_redis = _fake_db_manager_with_redis()

    runner = CliRunner()
    with patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.core.get_database_manager", return_value=fake_manager):
        result = runner.invoke(
            app, ["context", "delete", "botty-feature-X", "--keep-graph"], input="y\n",
        )

    assert result.exit_code == 0
    # GRAPH.DELETE not called when --keep-graph is set
    delete_calls = [
        call for call in fake_redis.execute_command.call_args_list
        if call.args and call.args[0] == "GRAPH.DELETE"
    ]
    assert len(delete_calls) == 0
    # Registry entry still removed
    assert "botty-feature-X" not in config_manager.load_context_config().contexts


def test_delete_still_removes_registry_when_graph_purge_fails(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    config_manager.create_context(
        "botty-feature-X",
        database="falkordb-remote",
        graph_name="botty-feature-X-graph",
        repo_path="/Users/dev/feat",
        created_via_fork=True,
    )

    fake_manager, fake_redis = _fake_db_manager_with_redis()
    # Make GRAPH.DELETE blow up with a non-idempotent error
    fake_redis.execute_command.side_effect = RuntimeError("connection reset by peer")

    runner = CliRunner()
    with patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.core.get_database_manager", return_value=fake_manager):
        result = runner.invoke(app, ["context", "delete", "botty-feature-X"], input="y\n")

    # CLI should still succeed (warning printed, registry cleaned)
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "botty-feature-X" not in config_manager.load_context_config().contexts


def test_delete_help_lists_keep_graph_flag():
    # NO_COLOR + TERM=dumb disables rich's ANSI escape codes, which otherwise
    # split flag names like `--keep-graph` across escape sequences in CI's narrow terminal.
    runner = CliRunner()
    result = runner.invoke(
        app, ["context", "delete", "--help"], env={"NO_COLOR": "1", "TERM": "dumb"}
    )
    assert result.exit_code == 0
    assert "--keep-graph" in result.output
