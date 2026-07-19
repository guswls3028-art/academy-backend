"""ProblemSegmentationProposal helper — Stage 3 Phase 3.2 (2026-05-06).

manual cut overlap validator + proposal 생성 helper.

원칙 (사용자 directive):
- 자동 분리 경로는 manual=true MatchupProblem row를 변경하지 않는다.
- manual_index 경로만 명시적 staff 승인 후 지정된 manual=true 문항을 갱신한다.
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


# ── Phase 3.3 (2026-05-06): approve / reject API helpers ────────────────
#
# proposal → MatchupProblem 승격 path. callback 미연결 — staff 명시 호출만.
#
# 원칙:
# - rejected proposal 은 영구적으로 승격 불가 (manual_overlap 포함).
# - approved 중복 승인 금지.
# - select_for_update 로 동시 승인 race 차단.
# - transaction.atomic — 승격 실패 시 proposal status 변경도 롤백.
# - selected_problem_ids / 기존 보고서 / comment 절대 미접근.
# - segmentation은 manual=true MatchupProblem을 변경하지 않는다.
# - manual_index만 target_problem을 잠그고 승인된 필드만 갱신한다.

_APPROVABLE_STATUSES = frozenset({"pending", "needs_review", "auto_passed"})
_PROMOTED_META_KEY_FROM_PROPOSAL = "approved_from_proposal_id"


class ProposalApprovalError(Exception):
    """approve_proposal / reject_proposal 검증 실패."""


def _approve_manual_index_proposal(proposal, user):
    """승인된 AI 인덱싱 결과만 기존 manual 문항에 적용한다."""
    import math

    from django.utils import timezone
    from apps.domains.matchup.models import MatchupProblem

    def validated_vector(name: str):
        value = payload.get(name)
        if value is None:
            return None
        if not isinstance(value, list) or not value or len(value) > 4096:
            raise ProposalApprovalError(
                f"manual_index {name} must be a non-empty numeric vector (id={proposal.id})"
            )
        if any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in value
        ):
            raise ProposalApprovalError(
                f"manual_index {name} contains a non-finite numeric value (id={proposal.id})"
            )
        return [float(item) for item in value]

    if not proposal.target_problem_id:
        raise ProposalApprovalError(
            f"manual_index proposal has no target problem (id={proposal.id})"
        )

    try:
        problem = MatchupProblem.objects.select_for_update().get(
            id=proposal.target_problem_id,
            tenant_id=proposal.tenant_id,
            document_id=proposal.document_id,
        )
    except MatchupProblem.DoesNotExist as exc:
        raise ProposalApprovalError(
            f"manual_index target problem is missing or out of scope (id={proposal.id})"
        ) from exc

    meta = dict(problem.meta or {})
    if meta.get("manual") is not True:
        raise ProposalApprovalError(
            f"manual_index target is not manual=true (id={proposal.id})"
        )

    payload = proposal.raw_response if isinstance(proposal.raw_response, dict) else {}
    embedding = validated_vector("embedding")
    image_embedding = validated_vector("image_embedding")
    update_fields: list[str] = []
    proposed_text = str(payload.get("text") or "").strip()
    current_text = (problem.text or "").strip()
    if embedding is not None and not proposed_text:
        raise ProposalApprovalError(
            f"manual_index text embedding has no source text (id={proposal.id})"
        )
    if current_text and proposed_text and current_text != proposed_text:
        raise ProposalApprovalError(
            f"manual_index target text changed after analysis (id={proposal.id})"
        )
    if proposed_text and not current_text:
        problem.text = proposed_text
        update_fields.append("text")

    if embedding is not None:
        problem.embedding = embedding
        update_fields.append("embedding")
    if image_embedding is not None:
        problem.image_embedding = image_embedding
        update_fields.append("image_embedding")

    proposed_format = str(payload.get("format") or "").strip()
    if proposed_format and proposed_format not in {"choice", "essay"}:
        raise ProposalApprovalError(
            f"manual_index format is invalid (id={proposal.id})"
        )
    if proposed_format and meta.get("format") in {None, "", "choice"}:
        meta["format"] = proposed_format
        problem.meta = meta
        update_fields.append("meta")

    if update_fields:
        problem.save(update_fields=[*update_fields, "updated_at"])

    proposal.status = "approved"
    proposal.reviewed_by = user if user is not None and getattr(user, "id", None) else None
    proposal.reviewed_at = timezone.now()
    proposal.promoted_problem = problem
    proposal.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "promoted_problem", "updated_at",
    ])
    logger.info(
        "approve_manual_index_proposal | id=%s target_problem_id=%s by_user=%s fields=%s",
        proposal.id,
        problem.id,
        getattr(user, "id", None) if user else None,
        update_fields,
    )
    return problem


def _validation_errors_have_manual_overlap(validation_errors: list[dict]) -> bool:
    """validation_errors 안에 code='manual_overlap' 가 하나라도 있으면 True."""
    for err in validation_errors or []:
        if isinstance(err, dict) and err.get("code") == "manual_overlap":
            return True
    return False


def _existing_problem_number_conflict(
    document_id: int, number: int,
) -> Optional[int]:
    """document 안에 같은 number 의 MatchupProblem 이 이미 있으면 그 id 반환.

    Stage 6.3N — approve_proposal pre-check (Option A).
    DB unique(document_id, number) IntegrityError 사전 차단 + 학원장 검수 path 로 유도.

    read-only SELECT — only('id').first(). 변경 없음.

    Returns:
        존재 시: 기존 MatchupProblem id (int)
        미존재: None
    """
    from apps.domains.matchup.models import MatchupProblem
    existing = (
        MatchupProblem.objects
        .filter(document_id=document_id, number=number)
        .only("id")
        .order_by("id")
        .first()
    )
    return existing.id if existing else None


@transaction.atomic
def approve_proposal(
    proposal_id: int,
    user,
    *,
    adjustments: Optional[dict] = None,
):
    """proposal을 승인해 MatchupProblem을 생성하거나 수동 문항 인덱스를 반영한다.

    Args:
        proposal_id: ProblemSegmentationProposal id.
        user: 승인자 (User 인스턴스 또는 None).
        adjustments: optional dict — bbox / text / image_key / embedding override.
            bbox 변경 시 manual_overlap 재검사.

    Returns:
        승격된 MatchupProblem 인스턴스.

    Raises:
        ProposalApprovalError:
            - proposal status 가 _APPROVABLE_STATUSES 밖 (rejected / approved 등)
            - validation_errors 에 manual_overlap 존재 (영구 차단)
            - adjusted bbox 가 manual cut 과 overlap
            - 동시성 race 등

    Side effects (transaction.atomic):
        - segmentation: 새 MatchupProblem 생성 (manual=False,
          confirmation_status='confirmed', approved_from_proposal_id=proposal.id, bbox 기록).
        - manual_index: 기존 manual=true 대상의 OCR/임베딩을 명시 승인으로만 갱신.
        - proposal.status='approved', reviewed_by, reviewed_at, promoted_problem 갱신.
        - selected_problem_ids 어떤 곳에도 반영 X.
        - segmentation 경로는 기존 manual=true MatchupProblem row 변경 X.
    """
    from django.utils import timezone
    from apps.domains.matchup.models import (
        MatchupProblem,
        ProblemSegmentationProposal,
    )

    proposal = ProblemSegmentationProposal.objects.select_for_update().get(id=proposal_id)

    # status transition 검증 — rejected 는 영구 차단.
    if proposal.status == "rejected":
        raise ProposalApprovalError(
            f"rejected proposal cannot be approved (id={proposal_id})"
        )
    if proposal.status == "approved":
        # 이미 승격됨 — idempotent 거절 (재승격 시 MatchupProblem 중복 생성 방지).
        raise ProposalApprovalError(
            f"proposal already approved (id={proposal_id})"
        )
    if proposal.status not in _APPROVABLE_STATUSES:
        raise ProposalApprovalError(
            f"invalid status for approval: {proposal.status} (id={proposal_id})"
        )

    if (proposal.proposal_kind or "segmentation") == "manual_index":
        if adjustments:
            raise ProposalApprovalError(
                f"manual_index proposal does not accept segmentation adjustments (id={proposal_id})"
            )
        return _approve_manual_index_proposal(proposal, user)

    # validation_errors 에 manual_overlap 있으면 영구 차단 (Phase 3.2 정책 보강).
    if _validation_errors_have_manual_overlap(proposal.validation_errors):
        raise ProposalApprovalError(
            f"proposal has manual_overlap validation error — permanently blocked "
            f"(id={proposal_id})"
        )

    bbox_for_problem = dict(proposal.bbox or {})
    text_override: Optional[str] = None
    image_key_override: Optional[str] = None
    embedding_override = None

    if adjustments:
        if "bbox" in adjustments:
            new_bbox = adjustments["bbox"]
            # adjusted bbox 로 manual_overlap 재검사
            overlaps, max_iou, conflict_id = overlaps_existing_manual(
                document_id=proposal.document_id,
                candidate_bbox=new_bbox,
                page_number=proposal.page_number,
            )
            if overlaps:
                raise ProposalApprovalError(
                    f"adjusted bbox overlaps existing manual cut "
                    f"(iou={max_iou:.3f}, conflict_problem_id={conflict_id}, id={proposal_id})"
                )
            # bbox_for_problem 도 dict 형식으로 정규화
            if isinstance(new_bbox, dict):
                bbox_for_problem = dict(new_bbox)
            elif isinstance(new_bbox, (list, tuple)) and len(new_bbox) == 4:
                vals = [float(v) for v in new_bbox]
                bbox_for_problem = {
                    "x": vals[0], "y": vals[1], "w": vals[2], "h": vals[3],
                    "norm": _looks_normalized(vals),
                }
        if "text" in adjustments:
            text_override = adjustments["text"]
        if "image_key" in adjustments:
            image_key_override = adjustments["image_key"]
        if "embedding" in adjustments:
            embedding_override = adjustments["embedding"]

    # Stage 6.3N — number_conflict pre-check (Option A 정책)
    # MatchupProblem 생성 직전에 unique(document_id, number) 충돌 사전 차단.
    # 충돌 시 자동 +1 / 901번대 임의 배정 X — 학원장 검수 path 로 유도.
    target_number = proposal.detected_problem_number
    conflict_problem_id = _existing_problem_number_conflict(
        document_id=proposal.document_id, number=target_number,
    )
    if conflict_problem_id is not None:
        # validation_errors 추가 (기존 내용 보존)
        new_errors = list(proposal.validation_errors or [])
        new_errors.append({
            "code": "number_conflict",
            "detail": (
                f"document {proposal.document_id} already has number {target_number} "
                f"(MatchupProblem id={conflict_problem_id})"
            ),
            "conflicting_problem_id": conflict_problem_id,
            "target_number": target_number,
        })
        proposal.status = "needs_review"
        proposal.validation_errors = new_errors
        proposal.save(update_fields=["status", "validation_errors", "updated_at"])
        raise ProposalApprovalError(
            f"number_conflict — document {proposal.document_id} already has number "
            f"{target_number} (id={proposal_id}, conflict_problem_id={conflict_problem_id})"
        )

    # MatchupProblem 생성 — Stage 4 strict allowlist 통과 자격 부여.
    new_meta = {
        "manual": False,                          # AI 결과 + 학원장 승인 (manual cut 과 별개)
        "confirmation_status": "confirmed",       # Stage 4 strict allowlist 통과 자격
        _PROMOTED_META_KEY_FROM_PROPOSAL: proposal.id,
        "engine": proposal.engine,
        "model_version": proposal.model_version,
        "approved_by_id": user.id if user is not None and getattr(user, "id", None) else None,
        "bbox": bbox_for_problem,
    }
    new_problem = MatchupProblem.objects.create(
        tenant_id=proposal.tenant_id,
        document_id=proposal.document_id,
        number=proposal.detected_problem_number,
        text=(text_override if text_override is not None else ""),
        image_key=(image_key_override if image_key_override is not None else (proposal.image_key or "")),
        embedding=embedding_override,
        meta=new_meta,
    )

    # proposal 갱신 — validation_errors 보존 (clear 안 함).
    proposal.status = "approved"
    proposal.reviewed_by = user if user is not None and getattr(user, "id", None) else None
    proposal.reviewed_at = timezone.now()
    proposal.promoted_problem = new_problem
    proposal.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "promoted_problem", "updated_at",
    ])

    logger.info(
        "approve_proposal | id=%s → problem_id=%s | doc=%s page=%s num=%s engine=%s by_user=%s",
        proposal_id, new_problem.id, proposal.document_id, proposal.page_number,
        proposal.detected_problem_number, proposal.engine,
        getattr(user, "id", None) if user else None,
    )

    return new_problem


@transaction.atomic
def reject_proposal(
    proposal_id: int,
    user,
    *,
    reason: str = "",
    code: str = "manual_reject",
):
    """proposal status='rejected' + audit 기록. MatchupProblem 생성 없음.

    Args:
        proposal_id: ProblemSegmentationProposal id.
        user: 거절자 (User 또는 None).
        reason: 거절 사유 (audit log).
        code: 거절 분류 (default 'manual_reject'. 'incorrect_segmentation' 등).

    Raises:
        ProposalApprovalError:
            - approved 상태는 reject 불가 (이미 운영 풀 진입).

    Side effects (transaction.atomic):
        - validation_errors append (기존 errors 보존).
        - status='rejected', reviewed_by, reviewed_at 갱신.
        - MatchupProblem 변경/생성 X.
        - selected_problem_ids 미접근.

    rejected → rejected 재호출은 idempotent (추가 reason 만 append).
    """
    from django.utils import timezone
    from apps.domains.matchup.models import ProblemSegmentationProposal

    proposal = ProblemSegmentationProposal.objects.select_for_update().get(id=proposal_id)

    if proposal.status == "approved":
        raise ProposalApprovalError(
            f"approved proposal cannot be rejected (id={proposal_id}) — "
            f"이미 운영 풀에 승격됨"
        )

    errors = list(proposal.validation_errors or [])
    errors.append({
        "code": code,
        "detail": reason,
        "by_user_id": user.id if user is not None and getattr(user, "id", None) else None,
    })
    proposal.validation_errors = errors
    proposal.status = "rejected"
    proposal.reviewed_by = user if user is not None and getattr(user, "id", None) else None
    proposal.reviewed_at = timezone.now()
    proposal.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "validation_errors", "updated_at",
    ])

    logger.info(
        "reject_proposal | id=%s | code=%s | reason=%s | by_user=%s",
        proposal_id, code, reason, getattr(user, "id", None) if user else None,
    )

    return proposal
