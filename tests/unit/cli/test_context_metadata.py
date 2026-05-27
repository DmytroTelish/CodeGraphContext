"""Round-trip tests for the new fork-aware ContextInfo fields.

`cgc context fork` needs three pieces of per-context metadata that the original
ContextInfo lacked:

- graph_name: which FalkorDB graph the context's data lives in (so multiple
  contexts can share a single FalkorDB instance without overwriting each other).
- repo_path: the worktree the context is bound to (so fork can rewrite paths
  and refresh knows what to re-index).
- created_via_fork: discriminator so `cgc context delete` can auto-purge the
  graph for fork-created contexts without affecting non-fork contexts.

These tests verify the dataclass defaults, YAML round-trip, and backward
compatibility with config files that pre-date the new fields.
"""
from pathlib import Path

import yaml

from codegraphcontext.cli import config_manager


def _redirect_config_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point config_manager's module-level paths at a tmp directory."""
    cfg_dir = tmp_path / ".codegraphcontext"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_manager, "CONTEXT_CONFIG_FILE", cfg_dir / "config.yaml")
    monkeypatch.setattr(
        config_manager, "_LEGACY_CONTEXT_CONFIG_FILE", cfg_dir / "cgc_config.yaml"
    )
    monkeypatch.setattr(config_manager, "_LEGACY_FALKORDB_PATH", cfg_dir / "global" / "falkordb.db")
    return cfg_dir


def test_contextinfo_defaults_are_safe_for_fork_fields():
    """New fork fields default to empty/False so existing call sites don't break."""
    ctx = config_manager.ContextInfo(name="legacy")
    assert ctx.graph_name == ""
    assert ctx.repo_path == ""
    assert ctx.created_via_fork is False


def test_contextinfo_roundtrip_persists_all_new_fields(tmp_path, monkeypatch):
    """A context populated with all new fields round-trips through YAML cleanly."""
    cfg_dir = _redirect_config_dir(tmp_path, monkeypatch)

    populated = config_manager.ContextInfo(
        name="botty-feature-3210",
        database="falkordb-remote",
        db_path=str(cfg_dir / "contexts" / "botty-feature-3210" / "db"),
        repos=[],
        cgcignore_path=str(cfg_dir / "contexts" / "botty-feature-3210" / ".cgcignore"),
        graph_name="botty-feature-3210-graph",
        repo_path="/Users/dev/repos/bbc-feat-3210",
        created_via_fork=True,
    )
    cfg = config_manager.ContextConfig(mode="named", contexts={populated.name: populated})

    config_manager.save_context_config(cfg)
    reloaded = config_manager.load_context_config()

    assert "botty-feature-3210" in reloaded.contexts
    rt = reloaded.contexts["botty-feature-3210"]
    assert rt.graph_name == "botty-feature-3210-graph"
    assert rt.repo_path == "/Users/dev/repos/bbc-feat-3210"
    assert rt.created_via_fork is True
    # Pre-existing fields still survive.
    assert rt.database == "falkordb-remote"
    assert rt.name == "botty-feature-3210"


def test_contextinfo_roundtrip_omits_defaults_from_yaml(tmp_path, monkeypatch):
    """When new fields hold their defaults, the YAML stays clean (no clutter)."""
    cfg_dir = _redirect_config_dir(tmp_path, monkeypatch)

    plain = config_manager.ContextInfo(name="botty-main", database="falkordb-remote")
    cfg = config_manager.ContextConfig(mode="named", contexts={"botty-main": plain})

    config_manager.save_context_config(cfg)

    # Inspect raw YAML — defaults should be absent.
    raw = yaml.safe_load((cfg_dir / "config.yaml").read_text())
    entry = raw["contexts"]["botty-main"]
    assert "graph_name" not in entry
    assert "repo_path" not in entry
    assert "created_via_fork" not in entry


def test_contextinfo_loads_legacy_config_without_new_fields(tmp_path, monkeypatch):
    """A config.yaml written before this change still loads with safe defaults."""
    cfg_dir = _redirect_config_dir(tmp_path, monkeypatch)

    # Simulate a pre-existing YAML file from before the new fields landed.
    legacy_yaml = {
        "version": 1,
        "mode": "named",
        "default_context": "",
        "contexts": {
            "old-ctx": {
                "database": "falkordb",
                "db_path": str(cfg_dir / "contexts" / "old-ctx" / "db" / "falkordb"),
                "repos": [],
                "cgcignore_path": str(cfg_dir / "contexts" / "old-ctx" / ".cgcignore"),
                # No graph_name, repo_path, or created_via_fork keys.
            }
        },
    }
    (cfg_dir / "config.yaml").write_text(yaml.dump(legacy_yaml))

    loaded = config_manager.load_context_config()
    ctx = loaded.contexts["old-ctx"]
    assert ctx.graph_name == ""
    assert ctx.repo_path == ""
    assert ctx.created_via_fork is False
