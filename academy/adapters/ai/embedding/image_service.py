# PATH: apps/worker/ai_worker/ai/embedding/image_service.py
"""
이미지 임베딩 (sentence-transformers CLIP).

목적: 카메라 사진/스캔본의 OCR 텍스트가 짧거나 부정확할 때, 이미지 자체의
시각적 유사도로 매치업 정확도를 보강. 텍스트 임베딩과 별도 공간이지만
ensemble (가중평균)으로 결합.

LLM 사용 안 함 — Vision Transformer (CLIP-ViT-B-32) image encoder만.

매치업 흐름:
  1. matchup_pipeline에서 각 problem.image_path → image embedding
  2. DB problem.image_embedding 컬럼에 jsonb 저장
  3. 검색 시 cosine_sim(text) * α + cosine_sim(image) * (1-α) ensemble
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# CLIP 모델 — 이미지 임베딩 (텍스트는 별도 sentence-transformer 사용 → 한국어 강함).
# clip-ViT-B-32: 표준 CLIP, 512차원 이미지 임베딩. ~340MB.
_CLIP_MODEL_NAME = "clip-ViT-B-32"

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except ImportError:
    SentenceTransformer = None  # type: ignore

_clip_model: Optional["SentenceTransformer"] = None


def _get_clip_model() -> "SentenceTransformer":
    global _clip_model
    if _clip_model is not None:
        return _clip_model
    if SentenceTransformer is None:
        raise RuntimeError("sentence-transformers not installed")
    _clip_model = SentenceTransformer(_CLIP_MODEL_NAME)
    return _clip_model


@dataclass
class ImageEmbeddingBatch:
    vectors: List[List[float]]


def get_image_embeddings(image_paths: List[str]) -> ImageEmbeddingBatch:
    """이미지 경로 리스트 → CLIP 임베딩 벡터 리스트.

    빈 입력은 빈 결과. 실패한 이미지는 None 또는 zero-vector로 채워질 수 있어
    호출자가 zero/None 체크 필요.
    """
    if not image_paths:
        return ImageEmbeddingBatch(vectors=[])

    try:
        from PIL import Image
    except ImportError:
        logger.error("PIL not installed — image embedding skipped")
        return ImageEmbeddingBatch(vectors=[[] for _ in image_paths])

    try:
        model = _get_clip_model()
    except Exception as e:
        logger.warning("CLIP model load failed: %s — image embeddings empty", e)
        return ImageEmbeddingBatch(vectors=[[] for _ in image_paths])

    images = []
    valid_indices = []
    for i, p in enumerate(image_paths):
        try:
            img = Image.open(p).convert("RGB")
            images.append(img)
            valid_indices.append(i)
        except Exception as e:
            logger.warning("image load failed (%s): %s", p, e)
    if not images:
        return ImageEmbeddingBatch(vectors=[[] for _ in image_paths])

    try:
        vectors = model.encode(images, convert_to_numpy=False)
        vectors_list = [list(map(float, v)) for v in vectors]
    except Exception as e:
        logger.warning("CLIP encode failed: %s", e)
        return ImageEmbeddingBatch(vectors=[[] for _ in image_paths])

    # valid_indices 순서대로 채워넣기
    out: List[List[float]] = [[] for _ in image_paths]
    for idx, vec in zip(valid_indices, vectors_list):
        out[idx] = vec
    return ImageEmbeddingBatch(vectors=out)
