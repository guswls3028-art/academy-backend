# PATH: apps/domains/matchup/reranker.py
"""
Cross-encoder reranker — 매치업 추천 정확도 향상.

흐름:
  1. bi-encoder cosine으로 top_k_candidates=20 1차 검색 (services에서)
  2. 본 모듈의 rerank()가 cross-encoder로 source vs 각 candidate 점수 계산
  3. 재정렬된 top_k 반환

모델: BAAI/bge-reranker-v2-m3 (다국어, 한국어 우수). 첫 호출 시 lazy load.
의존성: sentence-transformers + torch CPU + transformers — requirements.txt에 추가 필요.
컨테이너에 의존성이 없으면(BG load 실패) None 반환 → services는 휴리스틱 점수만 사용 (graceful fallback).

운영:
  - first-call latency: ~3-5초 (모델 로드 + 첫 추론)
  - subsequent: top_k=20 reranking ~150-300ms (t4g.medium CPU)
  - 메모리: bge-reranker-v2-m3 FP32 ~1GB. INT8 quantization 시 ~250MB.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# 환경변수로 모델 변경 가능 — 운영 중 다른 모델 시험할 때.
# 기본 모델: bge-reranker-base (~280MB, 다국어).
# v2-m3는 더 정확하지만 ~1.1GB로 t4g.medium 8GB EBS에서 디스크 압박 발생.
_MODEL_NAME = os.environ.get("MATCHUP_RERANKER_MODEL", "BAAI/bge-reranker-base")
_MAX_LENGTH = int(os.environ.get("MATCHUP_RERANKER_MAX_LEN", "512"))
_BATCH_SIZE = int(os.environ.get("MATCHUP_RERANKER_BATCH", "8"))

# False = 로드 실패 시 disabled (재시도 안 함). None = 아직 로드 시도 안 함.
_model: Optional[object] = None
_lock = threading.Lock()


def is_available() -> bool:
    """Cross-encoder가 로드 가능한 환경인지 확인 (의존성 + 모델)."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _try_load() -> Optional[object]:
    """Cross-encoder 모델 lazy load. 실패 시 None 반환 + disabled 표시."""
    try:
        from sentence_transformers import CrossEncoder
        model = CrossEncoder(_MODEL_NAME, max_length=_MAX_LENGTH)
        logger.info("RERANKER_LOADED | model=%s max_len=%d", _MODEL_NAME, _MAX_LENGTH)
        return model
    except ImportError as e:
        logger.warning("RERANKER_DEPS_MISSING | %s — fallback to bi-encoder only", e)
        return None
    except Exception:
        logger.exception("RERANKER_LOAD_FAIL — fallback to bi-encoder only")
        return None


def get_reranker():
    global _model
    if _model is False:
        return None  # 이전 시도에서 실패 — 재시도 X
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            loaded = _try_load()
            _model = loaded if loaded is not None else False
    return _model if _model is not False else None


def rerank(
    source_text: str, candidates: List[str], top_k: int = 10
) -> Optional[List[Tuple[int, float]]]:
    """source 문제 텍스트와 candidates(다른 문제들의 텍스트)를 cross-encoder로 평가.

    Returns:
        [(candidate_idx, score), ...] 점수 높은 순. 모델 미가용 시 None.
    """
    if not candidates:
        return []
    model = get_reranker()
    if model is None:
        return None

    src = (source_text or "")[:_MAX_LENGTH]
    pairs = [(src, (c or "")[:_MAX_LENGTH]) for c in candidates]
    try:
        scores = model.predict(pairs, batch_size=_BATCH_SIZE, show_progress_bar=False)
        scored = list(enumerate(scores.tolist() if hasattr(scores, "tolist") else list(scores)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
    except Exception:
        logger.exception("RERANKER_PREDICT_FAIL")
        return None


def warmup() -> bool:
    """컨테이너 시작 직후 호출해서 모델을 미리 로드(첫 사용자 latency 회피).
    Django ready() 또는 별도 management command에서 호출.
    """
    if not is_available():
        return False
    try:
        rerank("warmup", ["dummy candidate text"], top_k=1)
        return True
    except Exception:
        logger.exception("RERANKER_WARMUP_FAIL")
        return False
