from __future__ import annotations

import pytest

from api.security import validate_read_only_cypher


class TestCypherValidation:
    def test_read_only_query_passes(self):
        assert validate_read_only_cypher("MATCH (n) RETURN n") is None
        assert validate_read_only_cypher("MATCH (a)-[r]->(b) RETURN a, b, r") is None

    def test_create_blocked(self):
        err = validate_read_only_cypher("CREATE (n:Robot {name: 'test'})")
        assert err is not None
        assert "CREATE" in err

    def test_merge_blocked(self):
        err = validate_read_only_cypher("MERGE (n:Robot {name: 'test'})")
        assert err is not None
        assert "MERGE" in err

    def test_delete_blocked(self):
        err = validate_read_only_cypher("MATCH (n) DELETE n")
        assert err is not None
        assert "DELETE" in err

    def test_detach_delete_blocked(self):
        err = validate_read_only_cypher("MATCH (n) DETACH DELETE n")
        assert err is not None

    def test_set_blocked(self):
        err = validate_read_only_cypher("MATCH (n) SET n.foo = 'bar'")
        assert err is not None
        assert "SET" in err

    def test_drop_blocked(self):
        err = validate_read_only_cypher("DROP CONSTRAINT test")
        assert err is not None

    def test_load_csv_blocked(self):
        err = validate_read_only_cypher("LOAD CSV FROM 'file:///test.csv' AS row RETURN row")
        assert err is not None

    def test_call_db_blocked(self):
        err = validate_read_only_cypher("CALL db.indexes()")
        assert err is not None

    def test_call_apoc_blocked(self):
        err = validate_read_only_cypher("CALL apoc.help('test')")
        assert err is not None

    def test_case_insensitive(self):
        """Write keywords are caught regardless of case."""
        err = validate_read_only_cypher("create (n)")
        assert err is not None
        err = validate_read_only_cypher("Match (n) Delete n")
        assert err is not None
