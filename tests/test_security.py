from __future__ import annotations

import pytest

from api.security import validate_read_only_cypher
from loaders.web_loader import URLSafetyError, validate_public_http_url


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



class TestURLSafety:
    def test_public_http_url_passes(self, monkeypatch):
        monkeypatch.setattr(
            "loaders.web_loader.socket.getaddrinfo",
            lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))],
        )

        assert validate_public_http_url("https://example.com/docs") == "https://example.com/docs"

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/file.txt",
            "http://user:pass@example.com",
        ],
    )
    def test_disallowed_url_shapes_blocked(self, url):
        with pytest.raises(URLSafetyError):
            validate_public_http_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000",
            "http://127.0.0.1:7474",
            "http://0.0.0.0",
            "http://169.254.169.254/latest/meta-data",
            "http://10.0.0.1",
            "http://172.16.0.1",
            "http://192.168.1.1",
            "http://[::1]/",
        ],
    )
    def test_private_and_local_addresses_blocked(self, url):
        with pytest.raises(URLSafetyError):
            validate_public_http_url(url)

    def test_hostname_resolving_to_private_ip_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "loaders.web_loader.socket.getaddrinfo",
            lambda *args, **kwargs: [(None, None, None, None, ("192.168.1.10", 0))],
        )

        with pytest.raises(URLSafetyError):
            validate_public_http_url("https://example.com")
