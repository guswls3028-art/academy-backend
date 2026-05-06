"""ProblemSegmentationProposal helper — Stage 3 Phase 3.2 (2026-05-06).

manual cut overlap validator + proposal 생성 helper.

원칙 (사용자 directive):
- manual=true MatchupProblem row는 어떤 호출자도 변경 X (이 모듈은 SELECT만).
- manual=true bbox와 IoU > 0.3 인 proposal 은 status='rejected' + validation_errors
  에 manual_overlap reason 기록.
- selected_problem_ids / 기존 보고서 / comment 절대 미접근.
- proposal 은 추천 풀 진입 X (eligible_for_recommendation_qs 로 자동 필터).
- Phase 3.2 는 helper 만 추가. callbacks.py / find_similar / 다른 path 는 미변경.

bbox schema (운영 분포):
- manual cut: `MatchupProblem.meta.bbox_norm = [x, y, w, h]` 0~1 normalized
- 자동분리:    `MatchupProblem.meta.bbox = [x, y, w, h]` px (page width/height)
- proposal 모델: `bbox = {"x": ..., "y": ..., "w": ..., "h": ..., "norm": bool}`

helper 입력은 위 3 형식 모두 받고, 내부적으로 normalized (0~1) 좌표로 변환해서 IoU 비교.
변환 불가능한 경우 (px 인데 page_dim 없음) 보수적으로 manual_overlap=True 처리 — 사용자
manual cut 영역 보호가 옳은 default 행동.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from django.db import transaction

logger = logging.getLogger(__name__)


# manual cut 영역과 신규 proposal 영역의 IoU threshold.
# 0.3 = 두 박스 면적 30% 겹침. 설계 문서 STAGE_3_APPROVAL_API_DESIGN.md 참조.
MANUAL_OVERLAP_IOU_THRESHOLD = 0.3


def _looks_normalized(values: list[float]) -> bool:
    """[x, y, w, h] 가 0~1 normalized 인지 휴리스틱 검출. 모두 ≤ 1.5 면 norm 추정."""
    return all(0.0 <= v <= 1.5 for v in values)


def normalize_bbox(
    bbox_value: Any,
    page_width: Optional[float] = None,
    page_height: Optional[float] = None,
) -> Optional[Tuple[float, float, float, float]]:
    """다양한 bbox 입력을 normalized (0~1) (x, y, w, h) tuple 로 변환.

    입력 형식:
    - dict: {"x": .., "y": .., "w": .., "h": .., "norm": bool}
      norm=True 면 그대로, norm=False 면 page_w/page_h 로 나눔.
    - list/tuple: [x, y, w, h]
      값으로 normalized 여부 휴리스틱 판단 (모두 ≤ 1.5 면 norm).
      px 으로 판정되면 page_w/page_h 필요.
    - 기타: None 반환 (호출자가 보수적 처리).

    Returns:
        (x, y, w, h) 모두 0~1 (clamp 안 함 — 외부에 음수/>1 그대로 전달).
        변환 불가 시 None.
    """
    if not bbox_value:
        return None

    # dict 형식
    if isinstance(bbox_value, dict):
        try:
            x = float(bbox_value["x"])
            y = float(bbox_value["y"])
            w = float(bbox_value["w"])
            h = float(bbox_value["h"])
        except (KeyError, TypeError, ValueError):
            return None
        is_norm = bool(bbox_value.get("norm", False))
        if is_norm:
            return (x, y, w, h)
        if not page_width or not page_height:
            return None
        return (x / page_width, y / page_height, w / page_width, h / page_height)

    # list/tuple 형식
    if isinstance(bbox_value, (list, tuple)) and len(bbox_value) == 4:
        try:
            vals = [float(v) for v in bbox_value]
        except (TypeError, ValueError):
            return None
        if _looks_normalized(vals):
            return tuple(vals)  # type: ignore[return-value]
        if not page_width or not page_height:
            return None
        x, y, w, h = vals
        return (x / page_width, y / page_height, w / page_width, h / page_height)

    return None


def iou_normalized(
    box_a: Tuple[float, float, float, float],
    box_b: Tuple[float, float, float, float],
) -> float:
    """xywh normalized 두 박스의 IoU. 둘 다 같은 좌표계(0~1) 가정.

    Returns: 0.0 ~ 1.0. 면적 0 이거나 겹침 없으면 0.0.
    """
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b

    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0

    # intersection rectangle
    ix1 = max(ax, bx)
    iy1 = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0

    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def _get_page_dim(document, page_number: int) -> tuple[Optional[float], Optional[float]]:
    """document.meta['page_dimensions'] 에서 page_number 의 (w, h) 추출.

    page_number 는 1-based 또는 0-based 둘 다 시도 — page_dimensions 는 list 라서
    index = page_number - 1 우선, 그 다음 page_number 직접.
    """
    meta = getattr(document, "meta", None) or {}
    dims = meta.get("page_dimensions") or []
    if not dims:
        return (None, None)
    # 1-based 우선
    if 1 <= page_number <= len(dims):
        d = dims[page_number - 1]
        if isinstance(d, (list, tuple)) and len(d) >= 2:
            return (float(d[0]), float(d[1]))
    # 0-based fallback
    if 0 <= page_number < len(dims):
        d = dims[page_number]
        if isinstance(d, (list, tuple)) and len(d) >= 2:
            return (float(d[0]), float(d[1]))
    return (None, None)


def overlaps_existing_manual(
    document_id: int,
    candidate_bbox: Any,
    *,
    page_number: int = 0,
    threshold: float = MANUAL_OVERLAP_IOU_THRESHOLD,
) -> tuple[bool, float, Optional[int]]:
    """document_id 안의 manual=true MatchupProblem bbox 와 candidate_bbox 의 IoU 검사.

    Args:
        document_id: 검사 대상 MatchupDocument.
        candidate_bbox: 신규 proposal bbox. dict 또는 list 형식.
        page_number: candidate 의 페이지 (px 변환 시 page_dim 사용).
        threshold: overlap 판정 IoU. default 0.3 (사용자 directive).

    Returns:
        (overlaps: bool, max_iou: float, conflicting_problem_id: Optional[int]).
        - overlaps: max_iou > threshold 또는 변환 실패 (보수적 True).
        - 변환 불가 시 max_iou=-1.0 + overlaps=True (manual 보호 우선).

    동작:
        manual=true MatchupProblem 의 bbox_norm 만 SELECT (manual cut 만 보호 대상).
        UPDATE/DELETE 일절 없음 — read-only.
    """
    from apps.domains.matchup.models import MatchupDocument, MatchupProblem

    try:
        document = MatchupDocument.objects.only("id", "meta").get(id=document_id)
    except MatchupDocument.DoesNotExist:
        # document 없으면 manual cut 도 없음 — overlap 아님.
        return (False, 0.0, None)

    pw, ph = _get_page_dim(document, page_number)

    cand_norm = normalize_bbox(candidate_bbox, page_width=pw, page_height=ph)
    if cand_norm is None:
        # candidate bbox 변환 불가 — 보수적으로 manual_overlap=True 처리.
        # 사용자 directive: manual cut 영역 보호 우선. 호출자가 이 case 를 알 수 있도록
        # max_iou=-1.0 sentinel.
        logger.warning(
            "overlaps_existing_manual: candidate bbox 변환 실패 (doc=%s, page=%s, bbox=%r) "
            "→ 보수적 manual_overlap=True",
            document_id, page_number, candidate_bbox,
        )
        return (True, -1.0, None)

    # manual=true MatchupProblem 만 SELECT — read-only.
    manual_qs = (
        MatchupProblem.objects
        .filter(document_id=document_id, meta__contains={"manual": True})
        .only("id", "meta")
    )

    max_iou = 0.0
    conflict_id: Optional[int] = None
    for mp in manual_qs:
        meta = mp.meta or {}
        # manual cut 은 bbox_norm 사용 (운영 분포 4,267/4,270). 자동분리 bbox 도 안전성 위해 같이 검사.
        manual_bbox_value = meta.get("bbox_norm") or meta.get("bbox")
        if not manual_bbox_value:
            continue
        manual_norm = normalize_bbox(manual_bbox_value, page_width=pw, page_height=ph)
        if manual_norm is None:
            continue
        iou = iou_normalized(cand_norm, manual_norm)
        if iou > max_iou:
            max_iou = iou
            conflict_id = mp.id

    return (max_iou > threshold, max_iou, conflict_id)


@transaction.atomic
def create_proposal(
    *,
    tenant_id: int,
    document_id: int,
    page_number: int,
    detected_problem_number: int,
    bbox: Any,
    engine: str,
    model_version: str = "",
    confidence: float = 0.0,
    image_key: str = "",
    raw_response: Optional[dict] = None,
    analysis_version_key: str = "",
    auto_status: str = "pending",
):
    """ProblemSegmentationProposal 단건 생성. manual overlap 자동 reject.

    Args:
        tenant_id, document_id: 격리 baseline.
        page_number, detected_problem_number: AI 결과.
        bbox: dict 또는 list. normalize_bbox() 가 받는 형식.
        engine: ProblemSegmentationProposal.ENGINE_CHOICES.
        model_version, confidence, image_key, raw_response: AI 메타.
        analysis_version_key: 같은 batch 묶음 식별자.
        auto_status: 호출자가 명시 (default='pending'). manual_overlap 시 'rejected' 로 덮어씀.

    Returns:
        ProblemSegmentationProposal 인스턴스 (저장 후).

    동작:
        - manual_overlap 검사 → IoU > 0.3 면 status='rejected' + validation_errors 에
          {'code': 'manual_overlap', 'bbox_iou': ..., 'conflicting_problem_id': ...} 기록.
        - 기존 MatchupProblem 미변경 (read-only SELECT 만).
        - selected_problem_ids 미접근.
        - bbox 가 dict 가 아니면 proposal 모델용 dict 형식으로 정규화하여 저장.
    """
    from apps.domains.matchup.models import ProblemSegmentationProposal

    overlaps, max_iou, conflict_id = overlaps_existing_manual(
        document_id=document_id,
        candidate_bbox=bbox,
        page_number=page_number,
    )

    # bbox 를 proposal model 용 dict 로 정규화 (저장 형식 통일).
    if isinstance(bbox, dict):
        bbox_for_save = dict(bbox)
    elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            vals = [float(v) for v in bbox]
            is_norm = _looks_normalized(vals)
            bbox_for_save = {"x": vals[0], "y": vals[1], "w": vals[2], "h": vals[3], "norm": is_norm}
        except (TypeError, ValueError):
            bbox_for_save = {}
    else:
        bbox_for_save = {}

    status = auto_status
    validation_errors: list[dict] = []

    if overlaps:
        status = "rejected"
        validation_errors.append({
            "code": "manual_overlap",
            "bbox_iou": max_iou,
            "conflicting_problem_id": conflict_id,
            "threshold": MANUAL_OVERLAP_IOU_THRESHOLD,
        })

    proposal = ProblemSegmentationProposal.objects.create(
        tenant_id=tenant_id,
        document_id=document_id,
        analysis_version_key=analysis_version_key,
        page_number=page_number,
        bbox=bbox_for_save,
        detected_problem_number=detected_problem_number,
        engine=engine,
        model_version=model_version,
        confidence=confidence,
        status=status,
        image_key=image_key,
        raw_response=raw_response or {},
        validation_errors=validation_errors,
    )

    if overlaps:
        logger.info(
            "create_proposal manual_overlap rejected | doc=%s page=%s num=%s iou=%.3f conflict=%s",
            document_id, page_number, detected_problem_number, max_iou, conflict_id,
        )

    return proposal
