# apps/worker/ai/embedding/service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Literal

from academy.adapters.ai.config import AIConfig
from apps.shared.utils.vector import cosine_similarity  # noqa: F401 — re-export

EmbeddingBackendName = Literal["local", "openai"]


@dataclass
class EmbeddingBatch:
    vectors: List[List[float]]
    backend: EmbeddingBackendName


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


from apps.shared.utils.circuit_breaker import circuit_breaker as _circuit_breaker


@_circuit_breaker(
    name="openai_embedding",
    failure_threshold=5,
    window_seconds=30,
    cooldown_seconds=60,
)
def _embed_openai_call(client, model: str, safe_texts: List[str]) -> object:
    return client.embeddings.create(model=model, input=safe_texts)


def _embed_openai(texts: List[str]) -> EmbeddingBatch:
    cfg = AIConfig.load()

    # Quota 가드: 외부 OpenAI 호출만 카운트 (local backend는 자체 처리이므로 무한).
    from apps.domains.ai.services.quota import consume_ai_quota
    consume_ai_quota(kind="embedding_openai")

    client = _get_openai_client()
    # PII 가드: OpenAI에 inline 전화번호(010-XXXX-XXXX)가 평문으로 흘러가지 않도록
    # 패턴 마스킹. 임베딩 의미에 거의 영향 없음(같은 형태 토큰으로 치환).
    from apps.shared.utils.pii import mask_inline_phones
    safe_texts = [mask_inline_phones(t) for t in texts]
    response = _embed_openai_call(client, cfg.EMBEDDING_OPENAI_MODEL, safe_texts)
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
