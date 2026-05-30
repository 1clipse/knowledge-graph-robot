from __future__ import annotations

from graph import embeddings


def reset_embedding_state(monkeypatch):
    monkeypatch.setattr(embeddings, "_SESSION", None)
    monkeypatch.setattr(embeddings, "_TOKENIZER", None)
    monkeypatch.setattr(embeddings, "_ST_MODEL", None)
    monkeypatch.setattr(embeddings, "_BACKEND", None)
    monkeypatch.setattr(embeddings, "_MODEL_LOADING", False)
    monkeypatch.setattr(embeddings, "_MODEL_ERROR", None)
    embeddings._MODEL_LOADED.clear()


class TestEmbeddingReadiness:
    def test_init_model_wait_returns_false_when_loader_sets_error(self, monkeypatch):
        reset_embedding_state(monkeypatch)

        def fake_load():
            embeddings._MODEL_ERROR = "missing model"
            embeddings._MODEL_LOADING = False
            embeddings._MODEL_LOADED.set()

        monkeypatch.setattr(embeddings, "_load_model_sync", fake_load)

        assert embeddings.init_model(wait=True, timeout=1) is False
        assert embeddings.status()["ready"] is False
        assert embeddings.status()["error"] == "missing model"

    def test_init_model_wait_returns_true_when_loader_sets_session(self, monkeypatch):
        reset_embedding_state(monkeypatch)

        def fake_load():
            embeddings._SESSION = object()
            embeddings._TOKENIZER = object()
            embeddings._MODEL_ERROR = None
            embeddings._MODEL_LOADING = False
            embeddings._MODEL_LOADED.set()

        monkeypatch.setattr(embeddings, "_load_model_sync", fake_load)

        assert embeddings.init_model(wait=True, timeout=1) is True
        assert embeddings.status()["ready"] is True

    def test_get_session_raises_timeout(self, monkeypatch):
        reset_embedding_state(monkeypatch)

        class NeverSetEvent:
            def is_set(self):
                return False

            def wait(self, timeout=None):
                return False

            def set(self):
                return None

            def clear(self):
                return None

        monkeypatch.setattr(embeddings, "_MODEL_LOADED", NeverSetEvent())
        monkeypatch.setattr(embeddings, "init_model", lambda wait=False: False)

        try:
            embeddings._get_session_and_tokenizer(wait_timeout=0.01)
        except TimeoutError as exc:
            assert "timed out" in str(exc)
        else:
            raise AssertionError("Expected TimeoutError")
