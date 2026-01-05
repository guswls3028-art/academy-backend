# apps/worker/ai/embedding/service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Literal
import math

from apps.worker.ai_worker.ai.config import AIConfig

EmbeddingBackendName = Literal["local", "openai"]


@dataclass
class EmbeddingBatch:
    vectors: List[List[float]]
    backend: EmbeddingBackendName


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """
    cosine similarity normalized to 0~1
    """
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        return 0.0

    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y

    if na <= 0.0 or nb <= 0.0:
        return 0.0

    sim = dot / (math.sqrt(na) * math.sqrt(nb))
    sim = max(-1.0, min(1.0, sim))
    return (sim + 1.0) / 2.0


# -------- local (sentence-transformers) --------
try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:
    SentenceTransformer = None  # type: ignore

_local_model: Optional["SentenceTransformer"] = None


def _get_local_model() -> "SentenceTransformer":
    global _local_model
    if _local_model is not None:
        return _local_model

    if SentenceTransformer is None:
        raise RuntimeError("sentence-transformers not installed")

    cfg = AIConfig.load()
    _local_model = SentenceTransformer(cfg.EMBEDDING_LOCAL_MODEL)
    return _local_model


def _embed_local(texts: List[str]) -> EmbeddingBatch:
    model = _get_local_model()
    vectors = model.encode(texts, convert_to_numpy=False)
    vectors_list = [list(map(float, v)) for v in vectors]
    return EmbeddingBatch(vectors=vectors_list, backend="local")


# -------- openai --------
try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

_openai_client: Optional["OpenAI"] = None


def _get_openai_client() -> "OpenAI":
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    if OpenAI is None:
        raise RuntimeError("openai not installed")

    cfg = AIConfig.load()
    if not cfg.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")

    _openai_client = OpenAI(api_key=cfg.OPENAI_API_KEY)
    return _openai_client


def _embed_openai(texts: List[str]) -> EmbeddingBatch:
    cfg = AIConfig.load()
    client = _get_openai_client()
    response = client.embeddings.create(model=cfg.EMBEDDING_OPENAI_MODEL, input=texts)
    vectors = [list(map(float, d.embedding)) for d in response.data]
    return EmbeddingBatch(vectors=vectors, backend="openai")


def _choose_backend() -> EmbeddingBackendName:
    cfg = AIConfig.load()
    mode = (cfg.EMBEDDING_BACKEND or "auto").lower()

    if mode == "local":
        return "local"
    if mode == "openai":
        return "openai"

    # auto: local 가능하면 local
    if SentenceTransformer is not None:
        try:
            _get_local_model()
            return "local"
        except Exception:
            pass
    return "openai"


def get_embeddings(texts: List[str]) -> EmbeddingBatch:
    if not texts:
        return EmbeddingBatch(vectors=[], backend=_choose_backend())

    backend = _choose_backend()
    norm = [(t or "").strip() for t in texts]

    if backend == "local":
        return _embed_local(norm)
    return _embed_openai(norm)
