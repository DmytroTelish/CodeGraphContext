"""End-to-end-style tests for `cgc context fork`.

These tests mock the redis connection on the FalkorDB driver, so they verify
the orchestration without touching a live FalkorDB instance:

- Source context lookup + validation
- GRAPH.COPY redis command issued with the right arguments
- Path rewrite Cypher issued when source.repo_path != target.repo_path
- Path rewrite skipped via --no-path-rewrite
- Target context registered with created_via_fork=True and the right graph_name

Mocking is done at the redis-connection layer (the lowest one) so the
intermediate code paths (driver init, graph_fork helpers) run for real.
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


def _seed_source_context(name="botty-stage", graph_name="botty-stage-graph",
                         repo_path="/Users/dev/botty-back-client-stage"):
    """Create a source context in the registry (no FalkorDB activity)."""
    config_manager.create_context(
        name,
        database="falkordb-remote",
        graph_name=graph_name,
        repo_path=repo_path,
    )


def _fake_db_manager_with_mock_redis():
    """Returns a fake db_manager that the fork command can use without touching FalkorDB."""
    fake_redis = MagicMock()
    fake_graph = MagicMock()
    # A successful path-rewrite query returns a result whose result_set is [[<count>]]
    fake_rewrite_result = MagicMock()
    fake_rewrite_result.result_set = [[42]]
    fake_graph.query.return_value = fake_rewrite_result

    fake_driver = MagicMock()
    fake_driver.connection = fake_redis
    fake_driver.select_graph.return_value = fake_graph

    fake_manager = MagicMock()
    fake_manager._driver = fake_driver
    fake_manager.get_driver.return_value = MagicMock()  # session-wrapper proxy
    return fake_manager, fake_redis, fake_graph


def test_fork_issues_graph_copy_and_registers_target(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    _seed_source_context()

    fake_manager, fake_redis, fake_graph = _fake_db_manager_with_mock_redis()

    runner = CliRunner()
    with patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.core.get_database_manager", return_value=fake_manager):
        result = runner.invoke(
            app,
            [
                "context",
                "fork",
                "botty-stage",
                "botty-feature-3210",
                "--repo-path",
                "/Users/dev/bbc-feat-3210",
            ],
        )

    assert result.exit_code == 0, f"unexpected exit: {result.output}"

    # GRAPH.COPY was issued with the right source and a derived target name
    graph_copy_calls = [
        call for call in fake_redis.execute_command.call_args_list
        if call.args and call.args[0] == "GRAPH.COPY"
    ]
    assert len(graph_copy_calls) == 1, f"expected exactly 1 GRAPH.COPY, got: {fake_redis.execute_command.call_args_list}"
    args = graph_copy_calls[0].args
    assert args[1] == "botty-stage-graph"
    assert args[2] == "botty-feature-3210-graph"

    # Target context registered with fork flag and target paths
    cfg = config_manager.load_context_config()
    assert "botty-feature-3210" in cfg.contexts
    target = cfg.contexts["botty-feature-3210"]
    assert target.graph_name == "botty-feature-3210-graph"
    assert target.repo_path == "/Users/dev/bbc-feat-3210"
    assert target.created_via_fork is True
    assert target.database == "falkordb-remote"


def test_fork_runs_path_rewrite_when_paths_differ(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    _seed_source_context()

    fake_manager, _fake_redis, fake_graph = _fake_db_manager_with_mock_redis()

    runner = CliRunner()
    with patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.core.get_database_manager", return_value=fake_manager):
        result = runner.invoke(
            app,
            [
                "context",
                "fork",
                "botty-stage",
                "botty-feature-3210",
                "--repo-path",
                "/Users/dev/bbc-feat-3210",
            ],
        )
    assert result.exit_code == 0

    # Path rewrite Cypher ran exactly once, with the right prefixes
    assert fake_graph.query.call_count == 1
    cypher, params = fake_graph.query.call_args.args
    assert "n.path STARTS WITH $old_prefix" in cypher
    assert params["old_prefix"] == "/Users/dev/botty-back-client-stage"
    assert params["new_prefix"] == "/Users/dev/bbc-feat-3210"


def test_fork_skips_path_rewrite_with_no_path_rewrite_flag(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    _seed_source_context()

    fake_manager, _fake_redis, fake_graph = _fake_db_manager_with_mock_redis()

    runner = CliRunner()
    with patch("codegraphcontext.cli.main._load_credentials"), \
         patch("codegraphcontext.core.get_database_manager", return_value=fake_manager):
        result = runner.invoke(
            app,
            [
                "context",
                "fork",
                "botty-stage",
                "botty-feature-3210",
                "--repo-path",
                "/Users/dev/bbc-feat-3210",
                "--no-path-rewrite",
            ],
        )
    assert result.exit_code == 0

    # No path rewrite query
    assert fake_graph.query.call_count == 0


def test_fork_errors_when_source_context_missing(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    # Do NOT seed source.

    runner = CliRunner()
    result = runner.invoke(app, ["context", "fork", "nope", "target"])
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_fork_errors_when_target_already_exists(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    _seed_source_context()
    _seed_source_context("already-here", graph_name="x", repo_path="/x")

    runner = CliRunner()
    result = runner.invoke(app, ["context", "fork", "botty-stage", "already-here"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_fork_errors_when_source_has_no_graph_name(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    config_manager.create_context(
        "legacy",
        database="falkordb-remote",
        # No graph_name set — pre-fork-era context.
    )

    runner = CliRunner()
    result = runner.invoke(app, ["context", "fork", "legacy", "new-target"])
    assert result.exit_code != 0
    assert "no graph_name set" in result.output


def test_fork_help_lists_all_flags():
    # NO_COLOR + TERM=dumb disables rich's ANSI escape codes, which otherwise
    # split flag names like `--repo-path` across escape sequences in CI's narrow terminal.
    runner = CliRunner()
    result = runner.invoke(
        app, ["context", "fork", "--help"], env={"NO_COLOR": "1", "TERM": "dumb"}
    )
    assert result.exit_code == 0
    assert "--repo-path" in result.output
    assert "--graph-name" in result.output
    assert "--no-path-rewrite" in result.output
