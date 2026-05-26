from __future__ import annotations

import logging
import threading
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = None
_DIM = 384  # BGE-small default
_MODEL_LOADED = threading.Event()
_MODEL_ERROR: Optional[str] = None


def _load_model_sync():
    global _EMBEDDING_MODEL, _MODEL_ERROR
    try:
        from sentence_transformers import SentenceTransformer
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        _EMBEDDING_MODEL = SentenceTransformer(
            "BAAI/bge-small-zh-v1.5",
            device=device,
            local_files_only=True,
        )
        logger.info(f"Loaded BGE-small-zh embedding model on {device}")
    except ImportError:
        _MODEL_ERROR = "sentence-transformers not installed"
        logger.warning(_MODEL_ERROR)
    except Exception as e:
        _MODEL_ERROR = str(e)
        logger.warning(f"Failed to load embedding model: {e}")
    finally:
        _MODEL_LOADED.set()


def init_model() -> None:
    """Start background model loading. Safe to call multiple times."""
    if _MODEL_LOADED.is_set() or _EMBEDDING_MODEL is not None:
        return
    t = threading.Thread(target=_load_model_sync, daemon=True)
    t.start()


def _get_model():
    if not _MODEL_LOADED.is_set():
        _MODEL_LOADED.wait(timeout=120)
    if _MODEL_ERROR:
        logger.warning(f"Embedding model unavailable: {_MODEL_ERROR}")
    return _EMBEDDING_MODEL


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Generate embeddings for a list of texts."""
    model = _get_model()
    if model is None:
        return [[0.0] * _DIM for _ in texts]
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.tolist()


def embed_single(text: str) -> List[float]:
    """Generate embedding for a single text."""
    results = embed_texts([text])
    return results[0]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors (already normalized)."""
    return float(np.dot(a, b))


def vector_search(
    query_embedding: List[float],
    entity_embeddings: List[tuple],
    top_k: int = 10,
) -> List[tuple]:
    """Find top_k most similar entities via cosine similarity.

    Args:
        query_embedding: The query vector
        entity_embeddings: List of (name, labels, embedding) tuples
        top_k: Number of results to return

    Returns:
        List of (name, labels, similarity_score) tuples sorted by similarity desc
    """
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
