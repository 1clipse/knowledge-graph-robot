from __future__ import annotations

import logging
import os
import threading
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH = os.environ.get("EMBEDDING_MODEL_PATH", "BAAI/bge-m3")
_DIM = 1024  # BGE-M3 embedding dimension
_SESSION = None
_TOKENIZER = None
_MODEL_LOADED = threading.Event()
_MODEL_ERROR: Optional[str] = None


def _load_model_sync():
    global _SESSION, _TOKENIZER, _MODEL_ERROR
    try:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        onnx_path = os.path.join(_MODEL_PATH, "onnx", "model.onnx")
        if not os.path.exists(onnx_path):
            raise FileNotFoundError(f"ONNX model not found at {onnx_path}")

        _SESSION = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        _TOKENIZER = AutoTokenizer.from_pretrained(_MODEL_PATH, local_files_only=True)
        logger.info(f"Loaded BGE-M3 ONNX model (1024d) from {onnx_path}")
    except ImportError as e:
        _MODEL_ERROR = f"Missing dependency: {e}"
        logger.warning(_MODEL_ERROR)
    except Exception as e:
        _MODEL_ERROR = str(e)
        logger.warning(f"Failed to load embedding model: {e}")
    finally:
        _MODEL_LOADED.set()


def init_model() -> None:
    if _MODEL_LOADED.is_set() or _SESSION is not None:
        return
    t = threading.Thread(target=_load_model_sync, daemon=True)
    t.start()


def _get_session_and_tokenizer():
    if not _MODEL_LOADED.is_set():
        _MODEL_LOADED.wait(timeout=120)
    if _MODEL_ERROR:
        logger.warning(f"Embedding model unavailable: {_MODEL_ERROR}")
    return _SESSION, _TOKENIZER


def embed_texts(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    session, tokenizer = _get_session_and_tokenizer()
    if session is None or tokenizer is None:
        return [[0.0] * _DIM for _ in texts]

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
