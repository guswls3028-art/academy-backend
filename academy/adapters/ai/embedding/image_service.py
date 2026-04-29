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
import os
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# CLIP 모델 — 이미지 임베딩 (텍스트는 별도 sentence-transformer 사용 → 한국어 강함).
# clip-ViT-B-32: 표준 CLIP, 512차원 이미지 임베딩. ~340MB.
_CLIP_MODEL_NAME = "clip-ViT-B-32"

# 한 번에 인코딩할 이미지 수 상한.
# t4g.medium(2 vCPU, 4GB) 기준 batch 16 = 메모리 ~600MB peak, ~6s/batch.
# 무제한일 때(~400 images) OOM/wedge 사고 발생(2026-04-29). 환경변수로 조정.
_CLIP_BATCH_SIZE = max(1, int(os.getenv("CLIP_IMAGE_BATCH_SIZE", "16")))

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

    # 메모리 폭증 방지를 위해 batch 단위로 처리.
    # 이미지를 한꺼번에 PIL로 열면 PDF 1건당 400+ images로 4GB RAM 워커 OOM.
    out: List[List[float]] = [[] for _ in image_paths]
    total = len(image_paths)
    for start in range(0, total, _CLIP_BATCH_SIZE):
        end = min(start + _CLIP_BATCH_SIZE, total)
        batch_paths = image_paths[start:end]
        images = []
        local_indices: List[int] = []
        for offset, p in enumerate(batch_paths):
            try:
                img = Image.open(p).convert("RGB")
                images.append(img)
                local_indices.append(start + offset)
            except Exception as e:
                logger.warning("image load failed (%s): %s", p, e)
        if not images:
            continue
        try:
            vectors = model.encode(images, convert_to_numpy=False, batch_size=_CLIP_BATCH_SIZE)
            for idx, vec in zip(local_indices, vectors):
                out[idx] = list(map(float, vec))
        except Exception as e:
            logger.warning(
                "CLIP encode failed (batch %d-%d/%d): %s",
                start, end, total, e,
            )
        finally:
            # PIL Image fileobj/decoder 명시 해제 — 누수 시 다음 batch에서 메모리 누적.
            for img in images:
                try:
                    img.close()
                except Exception:
                    pass
    return ImageEmbeddingBatch(vectors=out)
