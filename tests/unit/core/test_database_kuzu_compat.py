from codegraphcontext.core.database_kuzu import KuzuSessionWrapper

class _FakeRawResult:
    def get_column_names(self):
        return []

    def has_next(self):
        return False

    def get_next(self):
        return []

class _FakeConn:
    def __init__(self, fail_with=None):
        self.queries = []
        self.fail_with = fail_with

    def execute(self, query, params):
        self.queries.append((query, params))
        if self.fail_with is not None:
            raise Exception(self.fail_with)
        return _FakeRawResult()


def _make_importers_query():
    return """
    MATCH (file:File)-[imp:IMPORTS]->(module:Module {name: $module_name})
    WHERE 1=1
    OPTIONAL MATCH (repo:Repository)-[:CONTAINS]->(file)
    RETURN DISTINCT
        file.path as importer_file_path,
        imp.line_number as import_line_number,
        file.is_dependency as file_is_dependency,
        repo.name as repository_name
    ORDER BY file.is_dependency ASC, file.path
    LIMIT 50
    """


def _make_coimports_query():
    return """
    MATCH (file:File)-[:IMPORTS]->(target_module:Module {name: $module_name})
    MATCH (file)-[imp:IMPORTS]->(other_module:Module)
    WHERE other_module <> target_module
    RETURN DISTINCT
        other_module.name as imported_module,
        imp.alias as import_alias
    ORDER BY other_module.name
    LIMIT 50
    """


def test_rewrites_module_deps_query_for_kuzu_scope_and_ordering():
    conn = _FakeConn()
    session = KuzuSessionWrapper(conn)

    session.run(_make_importers_query(), module_name="json")

    translated = conn.queries[0][0]
    assert "WITH file, imp, module" in translated
    assert "OPTIONAL MATCH (repo:Repository)-[:" in translated
    assert "]->(file)" in translated
    assert "ORDER BY file_is_dependency ASC, importer_file_path" in translated


def test_rewrites_coimports_order_by_alias():
    conn = _FakeConn()
    session = KuzuSessionWrapper(conn)

    session.run(_make_coimports_query(), module_name="json")

    translated = conn.queries[0][0]
    assert "ORDER BY imported_module" in translated


def test_fulltext_index_procedure_becomes_noop():
    conn = _FakeConn()
    session = KuzuSessionWrapper(conn)

    session.run("CALL db.idx.fulltext.createNodeIndex('Function', 'name', 'source', 'docstring')")

    translated = conn.queries[0][0]
    assert translated.strip() == "RETURN 1"


def test_fulltext_query_rewrites_to_substring_search():
    conn = _FakeConn()
    session = KuzuSessionWrapper(conn)

    session.run(
        """
        CALL db.index.fulltext.queryNodes(\"code_search_index\", $search_term) YIELD node, score
        WITH node, score
        WHERE (node:Function OR node:Class OR node:Variable)
        MATCH (node)<-[:CONTAINS]-(f:File)
        RETURN
            CASE WHEN node:Function THEN 'function' ELSE 'class' END as type,
            node.name as name, f.path as path,
            node.line_number as line_number, node.source as source,
            node.docstring as docstring, node.is_dependency as is_dependency
        ORDER BY score DESC
        LIMIT 20
        """,
        search_term="name:transform",
    )

    translated, params = conn.queries[0]
    assert "db.index.fulltext.queryNodes" not in translated
    assert "toLower(node.source) CONTAINS toLower($search_term_plain)" in translated
    assert params.get("search_term_plain") == "transform"


def test_fail_fast_disables_query_type_after_first_known_compat_error():
    conn = _FakeConn(fail_with="Binder exception: Variable file is not in scope.")
    session = KuzuSessionWrapper(conn)

    # First run records a failed execution and triggers the guard.
    session.run(_make_importers_query(), module_name="json")
    assert len(conn.queries) == 1

    # Second run should be skipped without executing on the connection.
    session.run(_make_importers_query(), module_name="json")
    assert len(conn.queries) == 1


def test_sanitize_value_normalizes_list_of_dict_keys_for_unwind_batches():
    mixed_batch = [
        {"name": "json", "line_number": 1},
        {"name": "pathlib", "full_import_name": "pathlib", "line_number": None},
    ]

    normalized = KuzuSessionWrapper._sanitize_value(mixed_batch)

    assert isinstance(normalized, list)
    assert len(normalized) == 2
    assert set(normalized[0].keys()) == set(normalized[1].keys())
    assert "full_import_name" in normalized[0]
    assert normalized[0]["full_import_name"] is None
    assert normalized[1]["line_number"] == -1


def test_sanitize_value_deduplicates_identical_batch_rows():
    batch = [
        {"name": "$n", "line_number": 3, "path": "/repo/a.php"},
        {"name": "$n", "line_number": 3, "path": "/repo/a.php"},
    ]

    normalized = KuzuSessionWrapper._sanitize_value(batch)

    assert len(normalized) == 1


def test_import_batch_rewrite_sets_missing_full_import_name():
    conn = _FakeConn()
    session = KuzuSessionWrapper(conn)
    session._skip_unwind_fallback = True

    session.run(
        """
        UNWIND $batch AS row
        MATCH (f:File {path: $file_path})
        MERGE (m:Module {name: row.name})
        SET m.alias = row.alias,
            m.full_import_name = coalesce(row.full_import_name, m.full_import_name)
        MERGE (f)-[r:IMPORTS]->(m)
        SET r.line_number = row.line_number,
            r.alias = row.alias
        """,
        file_path="/repo/a.py",
        batch=[{"name": "json", "line_number": 1}, {"name": "pathlib", "alias": "pl"}],
    )

    translated, params = conn.queries[0]
    assert "m.alias = row.alias" not in translated
    assert all("full_import_name" in row for row in params["batch"])


def test_unwind_uid_injection_uses_fallback_for_missing_pk_fields():
    conn = _FakeConn()
    session = KuzuSessionWrapper(conn)

    session.run(
        """
        UNWIND $batch AS row
        MERGE (n:Function {name: row.name, path: $file_path, line_number: row.line_number})
        SET n += row
        """,
        file_path="/repo/a.py",
        batch=[
            {"name": "$n", "line_number": None, "source": "x"},
            {"name": "$n", "line_number": None, "source": "y"},
        ],
    )

    translated, params = conn.queries[0]
    assert "uid: row.uid" in translated
    assert "MERGE (n:Function {uid: row.uid})" in translated
    assert all("uid" in row for row in params["batch"])
    assert params["batch"][0]["uid"] != params["batch"][1]["uid"]


def test_unwind_uid_collision_disambiguator_is_content_derived():
    """When multiple rows collide, the disambiguating suffix must come from row content (not batch index)."""
    conn = _FakeConn()
    session = KuzuSessionWrapper(conn)
    # Three rows that all produce identical raw composite keys. line_number is a
    # real (non-missing) shared value so the fallback-hash path doesn't kick in
    # and mask the collision before the disambiguator ever runs.
    row_x = {"name": "$n", "line_number": 1, "source": "x"}
    row_y = {"name": "$n", "line_number": 1, "source": "y"}
    row_z = {"name": "$n", "line_number": 1, "source": "z"}
    session.run(
        """
        UNWIND $batch AS row
        MERGE (n:Function {name: row.name, path: $file_path, line_number: row.line_number})
        SET n += row
        """,
        file_path="/repo/a.py",
        batch=[row_x, row_y, row_z],
    )
    _translated, params = conn.queries[0]
    # row_x is first → no suffix (no prior collision). row_y and row_z get suffixes.
    uids_by_source = {r["source"]: r["uid"] for r in params["batch"]}
    assert uids_by_source["x"] == "$n/repo/a.py1", uids_by_source
    # row_y and row_z suffixes must contain a hex digest (8 hex chars), not "#0"/"#1"/"#2".
    import re
    hex_suffix = re.compile(r"#[0-9a-f]{8}$")
    assert hex_suffix.search(uids_by_source["y"]), f"row_y UID lacks hex suffix: {uids_by_source['y']}"
    assert hex_suffix.search(uids_by_source["z"]), f"row_z UID lacks hex suffix: {uids_by_source['z']}"
    # And the suffixes for y and z must DIFFER (content-derived from different sources).
    assert uids_by_source["y"] != uids_by_source["z"]


def test_inheritance_queries_bypass_fail_fast_guard():
    session = KuzuSessionWrapper(_FakeConn())
    q = "MATCH (a)-[:INHERITS]->(b) MERGE (a)-[:INHERITS]->(b)"
    assert session._classify_query_type(q) == "inheritance_resolution"
    assert not session._should_fail_fast(
        "inheritance_resolution",
        Exception("Binder exception: Create rel  bound by multiple node labels is not supported."),
    )


def test_unwind_uid_seen_set_resets_per_merge_statement():
    """Two MERGE statements in one query must each get their own collision tracker."""
    conn = _FakeConn()
    session = KuzuSessionWrapper(conn)
    # Two MERGE statements, each with its own pair of colliding rows. line_number
    # is a real (non-missing) shared value so the fallback-hash path doesn't mask
    # the collision before the disambiguator ever runs.
    row_x = {"name": "$n", "line_number": 1, "source": "x"}
    row_y = {"name": "$n", "line_number": 1, "source": "y"}
    session.run(
        """
        UNWIND $batch AS row
        MERGE (n:Function {name: row.name, path: $file_path, line_number: row.line_number})
        MERGE (m:Class    {name: row.name, path: $file_path, line_number: row.line_number})
        SET n += row
        """,
        file_path="/repo/a.py",
        batch=[row_x, row_y],
    )
    _translated, params = conn.queries[0]
    # Both rows now have uid set. Inspect what _translate_query stored on them.
    # Because there are two MERGE statements, each MERGE's collision loop runs in
    # sequence and the second one overwrites item['uid']. We can't observe per-MERGE
    # uids from params, but we CAN assert the final uid on each row is the one the
    # SECOND MERGE assigned (which is itself consistent — row_x raw, row_y suffixed).
    uids = {r["source"]: r["uid"] for r in params["batch"]}
    assert uids["x"] == "$n/repo/a.py1", uids
    # row_y must have a hex suffix because it collided with row_x within the SECOND MERGE
    # (and within the first too — both MERGEs see the same collision pattern). The
    # critical invariant is the second MERGE ran its OWN seen_uids; if it had inherited
    # state from the first MERGE, row_x in the second pass would also have been suffixed.
    import re
    assert re.search(r"#[0-9a-f]{8}$", uids["y"]), uids


def test_unwind_uid_mixed_batch_only_suffixes_colliders():
    """Non-colliding rows in a mixed batch must keep their raw UID; only colliders get suffixed."""
    conn = _FakeConn()
    session = KuzuSessionWrapper(conn)
    # Three rows: two collide on (name="$n", line_number=1), one is unique. line_number
    # is a real (non-missing) shared value for the colliders so the fallback-hash path
    # doesn't mask the collision before the disambiguator ever runs.
    row_collider_1 = {"name": "$n", "line_number": 1, "source": "c1"}
    row_collider_2 = {"name": "$n", "line_number": 1, "source": "c2"}
    row_unique     = {"name": "foo", "line_number": 42,   "source": "u"}
    session.run(
        """
        UNWIND $batch AS row
        MERGE (n:Function {name: row.name, path: $file_path, line_number: row.line_number})
        SET n += row
        """,
        file_path="/repo/a.py",
        batch=[row_collider_1, row_unique, row_collider_2],
    )
    _translated, params = conn.queries[0]
    uids = {r["source"]: r["uid"] for r in params["batch"]}
    # collider_1 is the first occurrence of its raw UID → no suffix.
    assert uids["c1"] == "$n/repo/a.py1", uids
    # unique row has a different raw UID → no collision → no suffix.
    assert uids["u"] == "foo/repo/a.py42", uids
    # collider_2 hits a collision with collider_1 → gets a hex suffix.
    import re
    assert re.search(r"^\$n/repo/a\.py1#[0-9a-f]{8}$", uids["c2"]), uids
