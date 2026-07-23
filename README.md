# CodeGraphContext (fork)

This is [DmytroTelish](https://github.com/DmytroTelish)'s fork of upstream
[CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext) — an
MCP server and CLI toolkit that indexes local code into a graph database to
give AI assistants and developers structured, queryable context.

For general usage, installation, supported languages, and the full CLI
reference, see [upstream's README](https://github.com/CodeGraphContext/CodeGraphContext#readme)
and [docs](https://codegraphcontext.vercel.app/) — this fork doesn't change
any of that. This document covers only what's different here.

The `upgrade/0.4.x-from-fork` branch is rebased onto upstream `main`
(currently `v0.5.1` + one commit). Full patch history:

```bash
git remote add upstream https://github.com/Shashankss1205/CodeGraphContext.git
git log upstream/main..upgrade/0.4.x-from-fork
```

---

## Features added

### `cgc context fork` — clone a named context's graph in ~2 seconds

```bash
cgc context fork <source> <target> [--repo-path PATH] [--graph-name NAME] [--no-path-rewrite]
```

Server-side clone of a named context's FalkorDB graph via `GRAPH.COPY`,
with node paths rewritten from the source's `repo_path` to the target's,
in one command. Built to replace a ~5-10 minute cold-start `cgc index` for
every new feature-branch worktree: keep one canonical context (e.g.
`main`/`stage`) continuously indexed, then fork it per worktree instead of
re-indexing from scratch. Benchmarked at ~2s for ~300k nodes / ~44k files.

What it does:
1. Validates the source context exists and owns a `graph_name` (no
   `graph_name` means no FalkorDB graph to clone).
2. Validates the target name doesn't already exist.
3. Resolves the target's `graph_name` (default `<target>-graph`) and
   `repo_path` (default: current directory).
4. Issues `GRAPH.COPY <source>-graph <target>-graph` at the Redis level.
5. Rewrites `n.path` properties in the cloned graph from the source's
   `repo_path` to the target's via a single Cypher `UPDATE` (skippable with
   `--no-path-rewrite`, or a no-op when the paths already match).
6. Registers the target context with `created_via_fork=True`, so a later
   `cgc context delete` auto-purges the cloned graph instead of leaving an
   orphaned ~300k-node graph behind (override with `--keep-graph`).

Currently FalkorDB-only — forking from a context on another backend fails
with a clear message pointing at re-indexing as the fallback.

New module: `codegraphcontext.core.graph_fork` (`fork_graph`,
`rewrite_paths_in_graph`, `delete_graph` — thin wrappers around the redis
connection, all raising `GraphForkError` on failure).

Also added: `--graph-name`/`--repo-path` flags on `cgc context create`, so a
context can be seeded to own a dedicated FalkorDB graph up front (required
before it can be forked from).

### MCP daemon mode — one shared server for multiple clients

```bash
cgc mcp daemon [--socket-path PATH]
cgc mcp start --daemon-socket PATH   # or env CGC_MCP_DAEMON_SOCKET
```

A long-lived JSON-RPC daemon serving over a Unix socket, so multiple
stdio-based MCP clients (Cursor, Claude Code, Codex, ...) can share one
running `CodeGraphContext` server instead of each spawning its own. When
`--daemon-socket` is set on `mcp start` (or the env var is), it proxies
stdio traffic to the shared daemon, auto-starting it if it isn't already
running; pass `--no-daemon` to opt back into the legacy one-process-per-client
behavior.

Enabled by extracting `MCPServer.process_jsonrpc_request()` out of the stdio
read loop so both the daemon and the plain stdio path share one
implementation — the stdio behavior itself is unchanged.

Also added: an optional tool-call serialization lock
(`CGC_SERIALIZE_TOOL_CALLS=1`), which the daemon uses to serialize
concurrent Kuzu writes coming from multiple proxied clients. Disabled by
default; the plain in-process stdio path is unaffected either way.

---

## Bug fixes

- **UID collisions on `UNWIND`-batch writes with duplicate composite keys.**
  When a batch of rows produced identical composite primary-key components
  (e.g. a missing `line_number` normalized to a placeholder), the UID
  injector assigned the same `raw_uid` to distinct rows, silently collapsing
  them into one node on `MERGE`. Fixed by detecting per-`MERGE` collisions
  and appending a suffix only to colliding rows — derived from a content
  hash of the row (not batch index, which would make the suffix
  order-dependent across re-indexes of the same batch). Non-colliding
  batches are unaffected. (Upstream has since independently added its own
  fallback for missing PK fields, which narrows but doesn't eliminate this
  collision class — this fix is still needed on top of that.)

- **`_initialize_services` dropped `graph_name` when resolving a context.**
  Every CLI command that resolves a context by name (`cgc query --context
  foo`, `cgc stats --context foo`, `cgc find ... --context foo`, etc.) was
  silently binding to the wrong FalkorDB graph — `cgc context fork` would
  correctly create and populate the new graph, but no follow-up command
  could read it back, so a forked context appeared empty. Root cause:
  `_initialize_services` passed `db_path` to `get_database_manager` but not
  `graph_name`, so the FalkorDB singleton fell back to the
  `FALKORDB_GRAPH_NAME` env var / `codegraph` default instead of the
  context's own graph. Verified against a real FalkorDB instance
  (16k-file / 42k-function repo): before the fix, `cgc query --context
  <fork>` returned 0 rows for the fork's own graph and leaked rows from the
  source graph instead; after the fix, node counts on the forked context
  match the source exactly.

- **`MCPServer.run()` leaked the previous request's id into unrelated error
  responses.** The loop's error handler checked `'request' in locals()` to
  recover a request id for the JSON-RPC error response, but `request` was
  never reset between iterations — after a successful request followed by a
  parse failure on the *next* line, the error response echoed the prior
  request's id instead of reporting `"unknown"`. Fixed by resetting
  `request = None` at the top of every loop iteration. Pre-existing upstream
  bug, not introduced by this fork.

- **`NameError: debug_log` crashed indexing on any C++ method-linking
  failure.** The exception handler for C++ method linking in
  `persistence/writer.py` tried to log via `debug_log`, which the file never
  imported (only `info_logger`/`warning_logger` were). Since that except
  branch fires routinely for non-C++ projects, this crashed the entire
  indexing run instead of just skipping the failed link — breaking the
  parser-golden integration tests and the e2e DB-parity test in CI.

---

## Installing this fork

```bash
uv tool install "git+https://github.com/DmytroTelish/CodeGraphContext.git@upgrade/0.4.x-from-fork"
# or, from a local checkout:
uv tool install --force /path/to/this/checkout
```

Verify with `cgc --version` and `cgc context fork --help` / `cgc mcp daemon
--help`.

## Test coverage

Both features and all four bug fixes above ship with dedicated unit and
integration tests (~1,300 lines added across `tests/unit/cli/`,
`tests/unit/core/`, `tests/unit/server/`, and `tests/integration/`). After
rebasing onto upstream `v0.5.1`, the full local suite is green modulo a
handful of pre-existing failures confirmed present on a clean upstream
checkout too (stale parser/embedding fixtures unrelated to this fork).
