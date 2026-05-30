from __future__ import annotations

from api.routes import eval as eval_route


def test_eval_build_context_passes_db_to_ask_helper(monkeypatch):
    calls = {}

    def fake_build_context(db, question, top_k, max_hops):
        calls["args"] = (db, question, top_k, max_hops)
        return "ctx", [], []

    monkeypatch.setattr("api.routes.ask._build_context", fake_build_context)

    db = object()
    assert eval_route._build_context(db, "问题", 5, 3) == "ctx"
    assert calls["args"] == (db, "问题", 5, 3)
