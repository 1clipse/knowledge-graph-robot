from __future__ import annotations

import logging
import os
import threading
import traceback
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH = os.environ.get("EMBEDDING_MODEL_PATH", "BAAI/bge-m3")
_ENABLE_ST_FALLBACK = os.environ.get("KG_EMBEDDING_ST_FALLBACK", "0").lower() in {"1", "true", "yes"}
_DIM = 1024  # BGE-M3 embedding dimension
_SESSION = None
_TOKENIZER = None
_ST_MODEL = None
_BACKEND: Optional[str] = None
_MODEL_LOADING = False
_MODEL_LOADED = threading.Event()
_MODEL_LOCK = threading.Lock()
_MODEL_ERROR: Optional[str] = None


def _refresh_model_path() -> str:
    """Refresh model path from env before loading.

    api.app sets EMBEDDING_MODEL_PATH during lifespan; importing this module before
    lifespan should not freeze the old default path forever.
    """
    global _MODEL_PATH
    _MODEL_PATH = os.environ.get("EMBEDDING_MODEL_PATH", _MODEL_PATH)
    return _MODEL_PATH


def _load_model_sync():
    global _SESSION, _TOKENIZER, _ST_MODEL, _BACKEND, _MODEL_ERROR, _MODEL_LOADING
    try:
        model_path = _refresh_model_path()
        onnx_error: Optional[Exception] = None

        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer

            onnx_path = os.path.join(model_path, "onnx", "model.onnx")
            if not os.path.exists(onnx_path):
                raise FileNotFoundError(f"ONNX model not found at {onnx_path}")

            _SESSION = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
            _TOKENIZER = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
            _BACKEND = "onnx"
            _MODEL_ERROR = None
            logger.info(f"Loaded BGE-M3 ONNX model (1024d) from {onnx_path}")
            return
        except Exception as e:
            onnx_error = e
            logger.warning(f"ONNX embedding backend unavailable, trying sentence-transformers: {e}")

        if not _ENABLE_ST_FALLBACK:
            _MODEL_ERROR = (
                f"ONNX backend failed: {onnx_error}. Install onnxruntime or set "
                "KG_EMBEDDING_ST_FALLBACK=1 to try the heavier sentence-transformers backend."
            )
            logger.warning(f"Failed to load embedding model: {_MODEL_ERROR}")
            return

        try:
            from sentence_transformers import SentenceTransformer

            _ST_MODEL = SentenceTransformer(model_path, local_files_only=True)
            _BACKEND = "sentence-transformers"
            _MODEL_ERROR = None
            logger.info(f"Loaded embedding model with sentence-transformers from {model_path}")
        except BaseException as e:
            _MODEL_ERROR = f"ONNX backend failed: {onnx_error}; sentence-transformers backend failed: {e}"
            logger.warning(f"Failed to load embedding model: {_MODEL_ERROR}")
            logger.debug(traceback.format_exc())
    finally:
        _MODEL_LOADING = False
        _MODEL_LOADED.set()


def init_model(wait: bool = False, timeout: float | None = None) -> bool:
    """Start loading the embedding model and optionally wait for readiness.

    Returns True when the model is ready, False when loading failed or timed out.
    """
    global _MODEL_LOADING
    with _MODEL_LOCK:
        if (_SESSION is not None and _TOKENIZER is not None) or _ST_MODEL is not None:
            _MODEL_LOADED.set()
            return True
        if not _MODEL_LOADING and not _MODEL_LOADED.is_set():
            _MODEL_LOADING = True
            t = threading.Thread(target=_load_model_sync, daemon=True)
            t.start()

    if wait:
        _MODEL_LOADED.wait(timeout=timeout)
    return is_ready()


def is_ready() -> bool:
    has_onnx = _SESSION is not None and _TOKENIZER is not None
    return _MODEL_LOADED.is_set() and (has_onnx or _ST_MODEL is not None) and _MODEL_ERROR is None


def status() -> dict:
    return {
        "ready": is_ready(),
        "loaded": _MODEL_LOADED.is_set(),
        "loading": _MODEL_LOADING,
        "model_path": _MODEL_PATH,
        "backend": _BACKEND,
        "sentence_transformers_fallback": _ENABLE_ST_FALLBACK,
        "error": _MODEL_ERROR,
        "dimension": _DIM,
    }


def _get_model(wait_timeout: float = 120):
    init_model(wait=False)
    if not _MODEL_LOADED.is_set():
        loaded = _MODEL_LOADED.wait(timeout=wait_timeout)
        if not loaded:
            raise TimeoutError(f"Embedding model load timed out after {wait_timeout}s. Model path: {_MODEL_PATH}")
    if _MODEL_ERROR:
        raise RuntimeError(f"Embedding model unavailable. Model path: {_MODEL_PATH}. Error: {_MODEL_ERROR}")
    return _BACKEND, _SESSION, _TOKENIZER, _ST_MODEL


def _get_session_and_tokenizer(wait_timeout: float = 120):
    _, session, tokenizer, _ = _get_model(wait_timeout=wait_timeout)
    return session, tokenizer


def embed_texts(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    backend, session, tokenizer, st_model = _get_model()
    if backend == "sentence-transformers" and st_model is not None:
        embeddings = st_model.encode(
            texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    if session is None or tokenizer is None:
        raise RuntimeError(
            "Embedding model not available. "
            f"Model path: {_MODEL_PATH}. Error: {_MODEL_ERROR or 'Unknown'}"
        )

    results: List[List[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        inputs = tokenizer(
            batch,
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=8192,
        )
        outputs = session.run(
            ["sentence_embedding"],
            {"input_ids": inputs["input_ids"], "attention_mask": inputs["attention_mask"]},
        )
        results.extend(outputs[0].tolist())
    return results


def embed_single(text: str) -> List[float]:
    return embed_texts([text])[0]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    return float(np.dot(a, b))


def vector_search(
    query_embedding: List[float],
    entity_embeddings: List[tuple],
    top_k: int = 10,
) -> List[tuple]:
    if not entity_embeddings:
        return []
    scores = []
    for name, labels, emb in entity_embeddings:
        if not emb or all(v == 0.0 for v in emb):
            continue
        sim = cosine_similarity(query_embedding, emb)
        scores.append((name, labels, sim))
    scores.sort(key=lambda x: -x[2])
    return scores[:top_k]
