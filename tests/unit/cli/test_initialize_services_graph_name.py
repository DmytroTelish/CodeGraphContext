"""Regression test: `_initialize_services` must thread ctx.graph_name to FalkorDB.

Without this, every CLI command that resolves a context (cgc query/stats/find/...)
silently falls back to the FALKORDB_GRAPH_NAME env var or the 'codegraph' default
when the context owns a different graph. That makes `cgc context fork`'s target
graph unreachable via any normal command — fork creates it, but no one can query it.
"""
from unittest.mock import MagicMock, patch

import pytest

from codegraphcontext.cli import cli_helpers, config_manager


def _redirect_config_dir(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".codegraphcontext"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_manager, "CONTEXT_CONFIG_FILE", cfg_dir / "config.yaml")
    monkeypatch.setattr(
        config_manager, "_LEGACY_CONTEXT_CONFIG_FILE", cfg_dir / "cgc_config.yaml"
    )
    monkeypatch.setattr(
        config_manager, "_LEGACY_FALKORDB_PATH", cfg_dir / "global" / "falkordb.db"
    )
    return cfg_dir


def test_initialize_services_threads_context_graph_name_to_get_database_manager(
    tmp_path, monkeypatch
):
    """When --context resolves to a context with graph_name, that value
    must be forwarded to get_database_manager so the FalkorDB driver binds
    to the right named graph."""
    _redirect_config_dir(tmp_path, monkeypatch)

    config_manager.create_context(
        "smoke-target",
        database="falkordb-remote",
        graph_name="smoke-target-graph",
        repo_path="/tmp/smoke-target",
        created_via_fork=True,
    )

    # Force named mode so the context flag actually resolves
    cfg = config_manager.load_context_config()
    cfg.mode = "named"
    config_manager.save_context_config(cfg)

    captured = {}

    def fake_get_database_manager(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        mgr = MagicMock()
        mgr.get_driver = MagicMock()
        return mgr

    monkeypatch.setattr(cli_helpers, "get_database_manager", fake_get_database_manager)
    monkeypatch.setattr(cli_helpers, "GraphBuilder", MagicMock())
    monkeypatch.setattr(cli_helpers, "CodeFinder", MagicMock())
    monkeypatch.setattr(cli_helpers, "JobManager", MagicMock())
    monkeypatch.setattr(cli_helpers, "ensure_first_run_bootstrap", lambda: None)

    db_manager, *_rest, resolved = cli_helpers._initialize_services(
        cli_context_flag="smoke-target"
    )

    assert resolved.context_name == "smoke-target"
    assert resolved.graph_name == "smoke-target-graph"
    # The fix: graph_name must be in the kwargs we passed
    assert captured["kwargs"].get("graph_name") == "smoke-target-graph"


def test_initialize_services_passes_none_graph_name_for_legacy_contexts(
    tmp_path, monkeypatch
):
    """Contexts created before the graph_name field existed must not break;
    we pass `None` (not "") so the FalkorDB driver falls back to its env var."""
    _redirect_config_dir(tmp_path, monkeypatch)

    config_manager.create_context("legacy", database="falkordb-remote")

    cfg = config_manager.load_context_config()
    cfg.mode = "named"
    config_manager.save_context_config(cfg)

    captured = {}

    def fake_get_database_manager(*args, **kwargs):
        captured["kwargs"] = kwargs
        mgr = MagicMock()
        mgr.get_driver = MagicMock()
        return mgr

    monkeypatch.setattr(cli_helpers, "get_database_manager", fake_get_database_manager)
    monkeypatch.setattr(cli_helpers, "GraphBuilder", MagicMock())
    monkeypatch.setattr(cli_helpers, "CodeFinder", MagicMock())
    monkeypatch.setattr(cli_helpers, "JobManager", MagicMock())
    monkeypatch.setattr(cli_helpers, "ensure_first_run_bootstrap", lambda: None)

    cli_helpers._initialize_services(cli_context_flag="legacy")

    # Empty-string graph_name from a legacy context should be normalized to None
    # so the FalkorDB driver's env-var fallback path engages (and doesn't
    # accidentally bind to the literal graph "").
    assert captured["kwargs"].get("graph_name") is None
