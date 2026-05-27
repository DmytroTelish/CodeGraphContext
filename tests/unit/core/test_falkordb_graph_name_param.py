"""FalkorDB drivers accept graph_name parameter that overrides env var fallback.

This is the plumbing that lets named contexts own isolated graphs in a single
FalkorDB instance. Without this, `cgc context fork` can't keep multiple
contexts' graphs separate on the same host.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def reset_falkordb_singletons():
    """Reset the FalkorDB manager singletons between tests.

    Both FalkorDBManager and FalkorDBRemoteManager are singletons via
    __new__/_instance caching. Without reset, the first test's state leaks
    into subsequent tests.
    """
    yield
    for module_name, class_name in [
        ("codegraphcontext.core.database_falkordb", "FalkorDBManager"),
        ("codegraphcontext.core.database_falkordb_remote", "FalkorDBRemoteManager"),
    ]:
        try:
            import importlib
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name, None)
            if cls is not None:
                cls._instance = None
        except ImportError:
            pass


def test_remote_manager_uses_explicit_graph_name_over_env_var(monkeypatch):
    """When graph_name is passed explicitly, it wins over FALKORDB_GRAPH_NAME env."""
    monkeypatch.setenv("FALKORDB_HOST", "localhost")
    monkeypatch.setenv("FALKORDB_GRAPH_NAME", "from-env")

    from codegraphcontext.core.database_falkordb_remote import FalkorDBRemoteManager
    FalkorDBRemoteManager._instance = None  # ensure fresh init

    mgr = FalkorDBRemoteManager(graph_name="from-explicit-arg")
    assert mgr.graph_name == "from-explicit-arg"


def test_remote_manager_falls_back_to_env_var_when_no_explicit_arg(monkeypatch):
    """When graph_name is not passed, env var FALKORDB_GRAPH_NAME wins."""
    monkeypatch.setenv("FALKORDB_HOST", "localhost")
    monkeypatch.setenv("FALKORDB_GRAPH_NAME", "from-env")

    from codegraphcontext.core.database_falkordb_remote import FalkorDBRemoteManager
    FalkorDBRemoteManager._instance = None

    mgr = FalkorDBRemoteManager()
    assert mgr.graph_name == "from-env"


def test_remote_manager_falls_back_to_default_when_no_arg_or_env(monkeypatch):
    """When neither arg nor env is set, the existing 'codegraph' default applies."""
    monkeypatch.setenv("FALKORDB_HOST", "localhost")
    monkeypatch.delenv("FALKORDB_GRAPH_NAME", raising=False)

    from codegraphcontext.core.database_falkordb_remote import FalkorDBRemoteManager
    FalkorDBRemoteManager._instance = None

    mgr = FalkorDBRemoteManager()
    assert mgr.graph_name == "codegraph"


def test_resolved_context_carries_graph_name_from_named_context(tmp_path, monkeypatch):
    """resolve_context() with --context flag returns the context's graph_name."""
    from codegraphcontext.cli import config_manager

    cfg_dir = tmp_path / ".codegraphcontext"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_manager, "CONTEXT_CONFIG_FILE", cfg_dir / "config.yaml")
    monkeypatch.setattr(
        config_manager, "_LEGACY_CONTEXT_CONFIG_FILE", cfg_dir / "cgc_config.yaml"
    )
    monkeypatch.setattr(config_manager, "_LEGACY_FALKORDB_PATH", cfg_dir / "global" / "falkordb.db")

    populated = config_manager.ContextInfo(
        name="botty-feature-3210",
        database="falkordb-remote",
        db_path=str(cfg_dir / "contexts" / "botty-feature-3210" / "db"),
        graph_name="botty-feature-3210-graph",
    )
    cfg = config_manager.ContextConfig(mode="named", contexts={"botty-feature-3210": populated})
    config_manager.save_context_config(cfg)

    resolved = config_manager.resolve_context(cli_context="botty-feature-3210", cwd=tmp_path)
    assert resolved.context_name == "botty-feature-3210"
    assert resolved.graph_name == "botty-feature-3210-graph"


def test_resolved_context_graph_name_empty_when_not_set(tmp_path, monkeypatch):
    """Pre-existing contexts without graph_name resolve to empty (env-var fallback path)."""
    from codegraphcontext.cli import config_manager

    cfg_dir = tmp_path / ".codegraphcontext"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_manager, "CONTEXT_CONFIG_FILE", cfg_dir / "config.yaml")
    monkeypatch.setattr(
        config_manager, "_LEGACY_CONTEXT_CONFIG_FILE", cfg_dir / "cgc_config.yaml"
    )
    monkeypatch.setattr(config_manager, "_LEGACY_FALKORDB_PATH", cfg_dir / "global" / "falkordb.db")

    plain = config_manager.ContextInfo(
        name="botty-main",
        database="falkordb-remote",
        db_path=str(cfg_dir / "contexts" / "botty-main" / "db"),
    )
    cfg = config_manager.ContextConfig(mode="named", contexts={"botty-main": plain})
    config_manager.save_context_config(cfg)

    resolved = config_manager.resolve_context(cli_context="botty-main", cwd=tmp_path)
    assert resolved.context_name == "botty-main"
    assert resolved.graph_name == ""
