from __future__ import annotations

from types import SimpleNamespace

from api.deps import get_db, get_schema_manager
from api.app import health_check


class Request:
    def __init__(self):
        self.app = SimpleNamespace(state=SimpleNamespace())


def test_deps_return_none_when_state_attrs_missing():
    request = Request()

    assert get_db(request) is None
    assert get_schema_manager(request) is None


def test_health_returns_degraded_when_db_state_missing():
    request = Request()

    result = health_check(request)

    assert result["status"] == "degraded"
    assert result["database"] is False
