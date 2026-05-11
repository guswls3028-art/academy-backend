"""Hybrid YOLO + Gemini VLM verifier — false positive 제거.

basic_definition_2026_05_09 SSOT MVP Phase: V11 paradigm 한계 (recall OK, precision
0.55-0.7) 의 후처리 layer. PoC (`hybrid_vlm_filter_poc_v3`) 결과 doc 207 prec
0.548→0.968, doc 302 0.55→0.971 검증.

흐름:
  segment → questions (bbox + image_path)
  → filter_questions_by_hybrid_vlm()
    → 각 box → cv2 crop + JPEG 압축
    → Gemini 2.5 Flash classify (PROMPT 9-class)
    → 'problem' 만 keep, 그 외 silent drop
  → _upload_cropped_images (운영 path 그대로)

비용 (PoC 측정):
  - Gemini 2.5 Flash @ ~$0.001/call
  - doc 평균 50-100 box → ~$0.05-0.10/doc
  - cost_cap_calls 200 = max ~$0.2/doc 안전 cap

fail-soft 정책:
  - Gemini 실패 / timeout / quota → box raw keep (학원장 노동 손실 방지)
  - cost_cap 도달 → 나머지 raw keep
  - GEMINI_API_KEY 미설정 → 즉시 returnRaw (no-op)

ENV flag MATCHUP_HYBRID_VLM_TENANTS (콤마 구분 tenant id) 매치 시만 적용.
default 빈 list → 모든 doc no-op (운영 영향 0).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# 10-class classification — fragment reject 강화 (2026-05-10 자가 검수 fix).
# 자가 시각 검수 (doc 615 num 1-10) 결과 V11 가 한 문항을 발문/보기/선택지로 over-segmentation
# 하는 fragment 가 다수 발견 → 기존 prompt 가 fragment 도 'problem' 분류해 keep.
# 신규 'problem_fragment' 카테고리 + 명시적 '완전성' 기준으로 fragment silent drop.
PROMPT = """이 이미지는 한국어 시험지 PDF 의 일부 영역이다. 자동 문항분리(YOLO)가 검출한 박스 안 영역이다.

다음 중 하나로 분류하시오. 가장 중요한 기준은 '문항 1개가 완전히 포함되었는가'.

- problem: 시험 문항 1개의 **완전한** 본문 (발문 + 보기/그림/표 + 선택지 ① ② ③ ④ ⑤ 또는 답 영역).
  → 학원장이 그대로 매치업 검색에 사용할 수 있는 단위.
- problem_fragment: 문항의 **일부만** 잘린 영역 (발문만 / 보기만 / 선택지만 / 표만 / 마지막 줄 누락 등).
  → 매치업 검색에 사용 불가. 학원장이 다시 cut 해야 함.
- cover: 표지 (책 제목, 저자, 챕터 표지 등)
- chapter_intro: 단원 시작 / 단원 헤더 / 단원 분리 페이지
- concept_intro: 개념 설명 / 정의 / 이론 도입부 (문항 아닌 학습자료)
- explanation: 해설 / 정답 풀이
- table_of_contents: 목차
- header_label: 출처 라벨 / 회차 정보
- empty_or_decoration: 빈 영역 / 장식 / 페이지 헤더 푸터
- other: 기타

답변은 JSON 으로: {"classification": "<class>"}
예: {"classification": "problem"} 또는 {"classification": "problem_fragment"}
"""

# problem 만 keep — 그 외 모두 reject.
KEEP_CLASSES = {"problem"}

# Gemini classify 입력 image 의 max dimension (압축, 비용 절감).
_MAX_IMG_DIM = 800
_JPEG_QUALITY = 80


def classify_box_image(
    image_bytes: bytes,
    *,
    document_id: Optional[str | int] = None,
    tenant_id: Optional[str | int] = None,
) -> str:
    """단일 box image classify. Gemini 2.5 Flash 호출.

    Returns:
      - classification 문자열 (problem / cover / ...) 또는
      - '_error:<exc_name>' / '_empty' (fail-soft)
    """
    try:
        from academy.adapters.ai.detection.vlm_fallback import _gemini_request
    except ImportError:
        return "_error:no_gemini_adapter"

    import base64
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    parts = [
        {"text": PROMPT},
        {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
    ]
    try:
        # enforce_per_doc_cap=False (2026-05-11): VLM auto-split 이 같은 doc 의 50 cap
        # 소진 후 Hybrid filter 가 RuntimeError 로 fail-soft keep 하던 결함 fix.
        # Hybrid 는 자체 cost_cap_calls + tenant daily cap 으로 안전 관리.
        resp = _gemini_request(
            model="gemini-2.5-flash",
            parts=parts,
            response_schema_hint='{"classification": "..."}',
            document_id=document_id,
            tenant_id=tenant_id,
            enforce_per_doc_cap=False,
        )
    except Exception as e:
        return f"_error:{type(e).__name__}"

    cls = (resp.get("classification") or "").strip().lower()
    return cls or "_empty"


def filter_questions_by_hybrid_vlm(
    questions: List[Dict[str, Any]],
    *,
    document_id: Optional[str | int] = None,
    tenant_id: Optional[str | int] = None,
    cost_cap_calls: int = 200,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """questions 의 각 box 에 Gemini classify → 'problem' 만 keep.

    fail-soft: classify 실패 / cost cap 도달 시 box raw keep (학원장 노동 손실 방지).

    Returns:
      (filtered_questions, stats)
      stats = {input, kept, rejected, errors, api_calls, cost_cap_hit, by_class}
    """
    try:
        import cv2
    except ImportError:
        logger.warning("HYBRID_VLM_NO_CV2 | filter skipped, raw keep")
        return list(questions), {
            "input": len(questions), "kept": len(questions),
            "rejected": 0, "errors": 0, "api_calls": 0,
            "cost_cap_hit": False, "by_class": {}, "skipped_reason": "no_cv2",
        }

    kept: List[Dict[str, Any]] = []
    rejected_meta: List[Dict[str, Any]] = []
    errors = 0
    calls = 0
    by_class: Dict[str, int] = {}

    for q in questions:
        if calls >= cost_cap_calls:
            kept.append(q)
            continue

        try:
            image_path = q.get("image_path", "")
            bbox = q.get("bbox")
            if not image_path or not bbox:
                kept.append(q)
                continue
            img = cv2.imread(image_path)
            if img is None:
                kept.append(q)
                continue

            x, y, w, h = bbox
            x_i = max(0, int(x))
            y_i = max(0, int(y))
            x2 = min(img.shape[1], x_i + int(w))
            y2 = min(img.shape[0], y_i + int(h))
            if x2 <= x_i or y2 <= y_i:
                kept.append(q)
                continue
            crop = img[y_i:y2, x_i:x2]
            if crop.size == 0:
                kept.append(q)
                continue

            # 비용 / 속도를 위해 max 800px 이내 resize
            ch, cw = crop.shape[:2]
            if max(ch, cw) > _MAX_IMG_DIM:
                ratio = _MAX_IMG_DIM / float(max(ch, cw))
                new_w = max(1, int(cw * ratio))
                new_h = max(1, int(ch * ratio))
                crop = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_AREA)

            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
            if not ok:
                kept.append(q)
                continue

            cls = classify_box_image(
                buf.tobytes(),
                document_id=document_id,
                tenant_id=tenant_id,
            )
            calls += 1
            by_class[cls] = by_class.get(cls, 0) + 1

            if cls.startswith("_error") or cls == "_empty":
                # fail-soft — raw keep
                kept.append(q)
                if cls.startswith("_error"):
                    errors += 1
                continue

            if cls in KEEP_CLASSES:
                kept.append(q)
            else:
                rejected_meta.append({
                    "number": q.get("number"),
                    "page_index": q.get("page_index"),
                    "classification": cls,
                })

        except Exception as e:
            logger.warning(
                "HYBRID_VLM_BOX_FAIL | doc=%s | num=%s | err=%s",
                document_id, q.get("number"), e,
            )
            kept.append(q)
            errors += 1

    stats = {
        "input": len(questions),
        "kept": len(kept),
        "rejected": len(rejected_meta),
        "errors": errors,
        "api_calls": calls,
        "cost_cap_hit": calls >= cost_cap_calls,
        "by_class": by_class,
    }
    if rejected_meta:
        # 전체 reject list 가 너무 크면 head 만 log
        stats["rejected_sample"] = rejected_meta[:20]

    return kept, stats


def is_hybrid_vlm_enabled_for_tenant(tenant_id: int | str | None) -> bool:
    """ENV flag MATCHUP_HYBRID_VLM_TENANTS 매치 여부.

    빈 string / 미설정 → 모든 tenant no-op.
    "1,3,5" → tenant_id ∈ {1,3,5} 만 enabled.
    """
    if tenant_id is None:
        return False
    raw = os.environ.get("MATCHUP_HYBRID_VLM_TENANTS", "")
    enabled_ids = {int(t) for t in raw.split(",") if t.strip().isdigit()}
    if not enabled_ids:
        return False
    try:
        return int(tenant_id) in enabled_ids
    except (TypeError, ValueError):
        return False
