"""Helpers for forking and cleaning up FalkorDB graphs.

CGC normally hides FalkorDB behind a Neo4j-compatible driver wrapper, but
forking a graph is a server-side Redis-level operation (``GRAPH.COPY``) that
doesn't fit the Cypher abstraction. This module exposes three thin helpers
that operate directly on the underlying redis connection inside the FalkorDB
driver:

- ``fork_graph(db_manager, source, dest)`` clones an entire graph in place via
  ``GRAPH.COPY``. Server-side bulk copy; ~2s for ~300k nodes in practice.

- ``rewrite_paths_in_graph(db_manager, graph_name, old_prefix, new_prefix)``
  updates every ``n.path`` property that starts with ``old_prefix`` so it
  starts with ``new_prefix`` instead. This is the step that re-anchors a
  cloned graph to a different worktree path.

- ``delete_graph(db_manager, graph_name)`` removes a graph entirely via
  ``GRAPH.DELETE``. Used by ``cgc context delete`` on fork-created contexts
  to avoid orphan graphs.

All three require a FalkorDB-backed db_manager (local or remote). For other
backends, callers should detect the type and either skip or use a backend
equivalent.
"""
from typing import Any


class GraphForkError(RuntimeError):
    """Raised when a fork/rewrite/delete operation cannot complete."""


def _get_redis_connection(db_manager: Any):
    """Extract the underlying redis.Redis client from a FalkorDB manager.

    The FalkorDB Python client (the ``falkordb`` package) wraps a redis client
    in its ``connection`` attribute. We need raw redis access here because
    GRAPH.COPY isn't exposed through the Graph object's ``.query()`` interface.
    """
    # Trigger lazy driver init if it hasn't happened yet.
    db_manager.get_driver()
    falkordb_instance = getattr(db_manager, "_driver", None)
    if falkordb_instance is None:
        raise GraphForkError(
            "db_manager has no _driver attribute — is this a FalkorDB backend?"
        )
    redis_client = getattr(falkordb_instance, "connection", None)
    if redis_client is None:
        raise GraphForkError(
            "FalkorDB driver has no .connection attribute. "
            "Library version mismatch or non-FalkorDB driver?"
        )
    return redis_client


def fork_graph(db_manager: Any, source_graph: str, dest_graph: str) -> None:
    """Server-side clone of ``source_graph`` to ``dest_graph`` using GRAPH.COPY.

    Raises GraphForkError if ``dest_graph`` already exists; FalkorDB's
    GRAPH.COPY refuses to overwrite existing graphs.
    """
    if not source_graph or not dest_graph:
        raise GraphForkError("source and dest graph names must be non-empty")
    if source_graph == dest_graph:
        raise GraphForkError(f"source and dest graph names are identical: {source_graph!r}")

    redis_client = _get_redis_connection(db_manager)
    try:
        redis_client.execute_command("GRAPH.COPY", source_graph, dest_graph)
    except Exception as exc:
        raise GraphForkError(
            f"GRAPH.COPY {source_graph!r} -> {dest_graph!r} failed: {exc}"
        ) from exc


def rewrite_paths_in_graph(
    db_manager: Any,
    graph_name: str,
    old_prefix: str,
    new_prefix: str,
) -> int:
    """Rewrite every n.path that starts with old_prefix to start with new_prefix.

    Returns the number of nodes whose path was updated. This is the step that
    re-anchors a forked graph to the target context's worktree path.

    No-op (returns 0 without running the query) when old_prefix == new_prefix.
    """
    if old_prefix == new_prefix:
        return 0
    if not graph_name:
        raise GraphForkError("graph_name must be non-empty")

    db_manager.get_driver()
    falkordb_instance = db_manager._driver
    graph = falkordb_instance.select_graph(graph_name)

    # Cypher: only update nodes whose path starts with old_prefix. Use
    # substring() to splice the new prefix in front of the relative suffix.
    cypher = (
        "MATCH (n) WHERE n.path IS NOT NULL AND n.path STARTS WITH $old_prefix "
        "SET n.path = $new_prefix + substring(n.path, size($old_prefix)) "
        "RETURN count(n) AS updated"
    )
    try:
        result = graph.query(cypher, {"old_prefix": old_prefix, "new_prefix": new_prefix})
    except Exception as exc:
        raise GraphForkError(
            f"path rewrite on graph {graph_name!r} failed: {exc}"
        ) from exc

    result_set = getattr(result, "result_set", None) or []
    if not result_set:
        return 0
    try:
        return int(result_set[0][0])
    except (IndexError, TypeError, ValueError):
        return 0


def delete_graph(db_manager: Any, graph_name: str) -> None:
    """Delete a graph from the FalkorDB instance via GRAPH.DELETE.

    Idempotent for non-existent graphs in the sense that a "no such graph"
    error is swallowed (logged). Other errors propagate as GraphForkError.
    """
    if not graph_name:
        raise GraphForkError("graph_name must be non-empty")

    redis_client = _get_redis_connection(db_manager)
    try:
        redis_client.execute_command("GRAPH.DELETE", graph_name)
    except Exception as exc:
        message = str(exc).lower()
        if "no such" in message or "does not exist" in message or "key does not exist" in message:
            return
        raise GraphForkError(
            f"GRAPH.DELETE {graph_name!r} failed: {exc}"
        ) from exc
