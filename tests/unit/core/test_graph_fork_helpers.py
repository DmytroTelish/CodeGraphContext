"""Unit tests for the three graph_fork helpers: fork_graph, rewrite_paths_in_graph, delete_graph.

These verify the redis/cypher commands are issued with the right arguments
and that error-mapping (GraphForkError) catches the right cases. All FalkorDB
interaction is mocked at the driver-connection layer.
"""
from unittest.mock import MagicMock

import pytest

from codegraphcontext.core.graph_fork import (
    GraphForkError,
    delete_graph,
    fork_graph,
    rewrite_paths_in_graph,
)


def _fake_manager_with_connection_and_graph(query_result_count=0):
    """A minimal db_manager double exposing the attributes graph_fork pokes at."""
    fake_redis = MagicMock()
    fake_graph = MagicMock()
    fake_query_result = MagicMock()
    fake_query_result.result_set = [[query_result_count]] if query_result_count is not None else []
    fake_graph.query.return_value = fake_query_result

    fake_falkordb_instance = MagicMock()
    fake_falkordb_instance.connection = fake_redis
    fake_falkordb_instance.select_graph.return_value = fake_graph

    fake_manager = MagicMock()
    fake_manager._driver = fake_falkordb_instance
    fake_manager.get_driver.return_value = MagicMock()  # whatever the wrapper would be
    return fake_manager, fake_redis, fake_graph


# ----- fork_graph -----------------------------------------------------------


def test_fork_graph_issues_graph_copy_with_source_and_dest():
    mgr, redis, _graph = _fake_manager_with_connection_and_graph()
    fork_graph(mgr, "src-graph", "dest-graph")
    redis.execute_command.assert_called_once_with("GRAPH.COPY", "src-graph", "dest-graph")


def test_fork_graph_rejects_empty_names():
    mgr, _redis, _graph = _fake_manager_with_connection_and_graph()
    with pytest.raises(GraphForkError, match="must be non-empty"):
        fork_graph(mgr, "", "x")
    with pytest.raises(GraphForkError, match="must be non-empty"):
        fork_graph(mgr, "x", "")


def test_fork_graph_rejects_identical_names():
    mgr, _redis, _graph = _fake_manager_with_connection_and_graph()
    with pytest.raises(GraphForkError, match="identical"):
        fork_graph(mgr, "same", "same")


def test_fork_graph_wraps_underlying_redis_errors():
    mgr, redis, _graph = _fake_manager_with_connection_and_graph()
    redis.execute_command.side_effect = RuntimeError("graph already exists")
    with pytest.raises(GraphForkError, match="GRAPH.COPY"):
        fork_graph(mgr, "src", "dest")


# ----- rewrite_paths_in_graph -----------------------------------------------


def test_rewrite_paths_issues_cypher_with_prefix_params():
    mgr, _redis, graph = _fake_manager_with_connection_and_graph(query_result_count=12)
    updated = rewrite_paths_in_graph(mgr, "g", "/old/path", "/new/path")
    assert updated == 12
    cypher, params = graph.query.call_args.args
    assert "STARTS WITH $old_prefix" in cypher
    assert params == {"old_prefix": "/old/path", "new_prefix": "/new/path"}


def test_rewrite_paths_is_noop_when_prefixes_match():
    mgr, _redis, graph = _fake_manager_with_connection_and_graph()
    updated = rewrite_paths_in_graph(mgr, "g", "/same", "/same")
    assert updated == 0
    graph.query.assert_not_called()


def test_rewrite_paths_returns_zero_on_empty_result_set():
    mgr, _redis, graph = _fake_manager_with_connection_and_graph(query_result_count=None)
    # graph.query returns a result with empty result_set
    updated = rewrite_paths_in_graph(mgr, "g", "/a", "/b")
    assert updated == 0


def test_rewrite_paths_wraps_cypher_errors():
    mgr, _redis, graph = _fake_manager_with_connection_and_graph()
    graph.query.side_effect = RuntimeError("cypher syntax error")
    with pytest.raises(GraphForkError, match="path rewrite"):
        rewrite_paths_in_graph(mgr, "g", "/a", "/b")


# ----- delete_graph ---------------------------------------------------------


def test_delete_graph_issues_graph_delete():
    mgr, redis, _graph = _fake_manager_with_connection_and_graph()
    delete_graph(mgr, "doomed-graph")
    redis.execute_command.assert_called_once_with("GRAPH.DELETE", "doomed-graph")


def test_delete_graph_swallows_no_such_graph_error():
    mgr, redis, _graph = _fake_manager_with_connection_and_graph()
    redis.execute_command.side_effect = RuntimeError("no such key in database")
    # Should NOT raise — deletion of a missing graph is idempotent.
    delete_graph(mgr, "missing")


def test_delete_graph_propagates_unexpected_errors():
    mgr, redis, _graph = _fake_manager_with_connection_and_graph()
    redis.execute_command.side_effect = RuntimeError("connection reset")
    with pytest.raises(GraphForkError, match="GRAPH.DELETE"):
        delete_graph(mgr, "anything")
