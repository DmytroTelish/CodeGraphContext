"""`cgc context create` accepts --graph-name and --repo-path flags.

These flags surface the per-context FalkorDB graph identity and worktree
binding introduced for the fork workflow. Verified via the typer test runner
plus the underlying create_context() function.
"""
from unittest.mock import patch

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


def test_create_context_function_persists_graph_name_and_repo_path(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)

    ok = config_manager.create_context(
        "botty-stage",
        database="falkordb-remote",
        graph_name="botty-stage-graph",
        repo_path="/Users/dev/botty-back-client-stage",
    )
    assert ok is True

    cfg = config_manager.load_context_config()
    ctx = cfg.contexts["botty-stage"]
    assert ctx.graph_name == "botty-stage-graph"
    assert ctx.repo_path == "/Users/dev/botty-back-client-stage"
    assert ctx.created_via_fork is False


def test_create_context_function_marks_created_via_fork_when_requested(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)

    config_manager.create_context(
        "botty-feature-X",
        database="falkordb-remote",
        graph_name="botty-feature-X-graph",
        repo_path="/Users/dev/bbc-feat-X",
        created_via_fork=True,
    )

    ctx = config_manager.load_context_config().contexts["botty-feature-X"]
    assert ctx.created_via_fork is True


def test_cli_context_create_help_lists_new_flags():
    # NO_COLOR + TERM=dumb disables rich's ANSI escape codes, which otherwise
    # split flag names like `--graph-name` across escape sequences in CI's narrow terminal.
    runner = CliRunner()
    result = runner.invoke(
        app, ["context", "create", "--help"], env={"NO_COLOR": "1", "TERM": "dumb"}
    )
    assert result.exit_code == 0
    assert "--graph-name" in result.output
    assert "--repo-path" in result.output


def test_cli_context_create_passes_flags_through_to_function(tmp_path, monkeypatch):
    _redirect_config_dir(tmp_path, monkeypatch)
    runner = CliRunner()

    with patch.object(config_manager, "create_context", wraps=config_manager.create_context) as spy:
        result = runner.invoke(
            app,
            [
                "context",
                "create",
                "botty-main",
                "--database",
                "falkordb-remote",
                "--graph-name",
                "botty-main-graph",
                "--repo-path",
                "/Users/dev/main",
            ],
        )
        assert result.exit_code == 0
        # Function called with the flags we passed
        kwargs = spy.call_args.kwargs
        assert kwargs["graph_name"] == "botty-main-graph"
        assert kwargs["repo_path"] == "/Users/dev/main"
