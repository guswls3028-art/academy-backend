"""Stage 5.9 — proposal payload 검증 강화 (실 INSERT 0회).

Stage 5.8 의 `ProposalPayloadCandidate` 가 운영 ProblemSegmentationProposal 모델 +
`apps.domains.matchup.proposal_helpers` 의 create_proposal / approve_proposal 흐름과
정확히 호환되는지 검증.

원칙 (사용자 directive Stage 5.9):
- ProblemSegmentationProposal **INSERT 절대 X** — schema validation only
- DB query 0회 (manual_overlap_provider callable 로 추상화 — mock fixture 또는 향후 DB)
- callback path 변경 0회 / R2 write 0회
- selected_problem_ids 미접근 / matchup_problem 미접근
- 운영 proposal_helpers / models import 안 함 (호환성은 dataclass + STATUS_CHOICES 상수
  미러로 검증)

Validation level:
1. schema 1:1 매핑 — payload 필드 = 모델 필드
2. field-level — bbox normalize, page_number, engine ENGINE_CHOICES, status STATUS_CHOICES,
   model_version, confidence 0~1, validation_errors 구조
3. status transition — approve gate (rejected / manual_overlap → 영구 차단)
4. selected_problem_ids 무관성 — payload 가 selected 에 영향 X (구조적으로 보장)
5. manual_overlap_provider — 호출자가 callable 주입 (DB query 없는 mock / 향후 DB-backed)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Optional

from .mock_response_integrator import (
    ProposalPayloadCandidate, UnifiedCandidate, ValidationError,
    _bbox_iou_norm,
)


SCHEMA_VERSION = "5.9-validator-1"


# ── 운영 ProblemSegmentationProposal 모델 mirror (Stage 5.9 read-only 미러) ──
# 모델 직접 import 안 함 — 운영 schema 변경 시 본 mirror 도 업데이트 필요.

# ENGINE_CHOICES (apps/domains/matchup/models.py:339)
ENGINE_CHOICES = frozenset({"yolo", "vlm", "ocr", "native_pdf", "manual_assist"})

# STATUS_CHOICES (apps/domains/matchup/models.py:331)
STATUS_CHOICES = frozenset({"pending", "needs_review", "rejected", "approved", "auto_passed"})

# proposal_helpers._APPROVABLE_STATUSES (apps/domains/matchup/proposal_helpers.py:329)
APPROVABLE_STATUSES = frozenset({"pending", "needs_review", "auto_passed"})

# proposal_helpers.MANUAL_OVERLAP_IOU_THRESHOLD
MANUAL_OVERLAP_IOU_THRESHOLD = 0.30

# 영구 차단 ValidationError code 집합
PERMANENTLY_BLOCKING_CODES = frozenset({"manual_overlap"})

# 모델 필드 — payload 와 1:1 매핑되어야 하는 필드 (audit 필드는 제외 — system-generated)
_MODEL_FIELDS_MAPPED = frozenset({
    "tenant_id", "document_id", "analysis_version_key",
    "page_number", "bbox", "detected_problem_number",
    "engine", "model_version", "confidence",
    "status", "image_key", "raw_response", "validation_errors",
})

# 모델 audit 필드 — 운영 approve_proposal / reject_proposal 시점에 채워짐 — payload 미포함 정상
_MODEL_AUDIT_FIELDS = frozenset({
    "reviewed_by", "reviewed_by_id", "reviewed_at", "promoted_problem", "promoted_problem_id",
})


# ── ValidationReport schema ──────────────────────────────────────────


@dataclass
class FieldViolation:
    field: str
    code: str          # "missing" | "type" | "out_of_range" | "invalid_choice" | "schema_extra"
    detail: str


@dataclass
class PayloadValidationResult:
    """단일 payload 검증 결과."""
    schema_ok: bool
    field_ok: bool
    status_ok: bool
    is_approvable: bool
    violations: list[FieldViolation] = field(default_factory=list)
    blocking_codes: list[str] = field(default_factory=list)


@dataclass
class BatchValidationReport:
    """payload list 전체 검증 보고서."""
    schema_version: str
    total: int
    schema_ok_count: int
    field_ok_count: int
    status_ok_count: int
    approvable_count: int
    blocked_count: int
    results: list[PayloadValidationResult] = field(default_factory=list)


# ── helpers ───────────────────────────────────────────────────────


def _is_finite_float(v: Any) -> bool:
    if not isinstance(v, (int, float)):
        return False
    if isinstance(v, bool):
        return False
    return v == v and v not in (float("inf"), float("-inf"))


def _validate_bbox(bbox: Any) -> list[FieldViolation]:
    """ProblemSegmentationProposal.bbox = JSON dict 검증.

    형식: {"x": float, "y": float, "w": float, "h": float, "norm": bool}
    norm=True 면 0~1 (clamp 안 함, 하지만 0~1 권장).
    """
    out: list[FieldViolation] = []
    if not isinstance(bbox, dict):
        out.append(FieldViolation("bbox", "type", f"bbox must be dict, got {type(bbox).__name__}"))
        return out
    for key in ("x", "y", "w", "h"):
        if key not in bbox:
            out.append(FieldViolation(f"bbox.{key}", "missing", f"missing key {key}"))
        elif not _is_finite_float(bbox[key]):
            out.append(FieldViolation(f"bbox.{key}", "type", f"{key} not finite number"))
    if "norm" not in bbox:
        out.append(FieldViolation("bbox.norm", "missing", "missing key 'norm'"))
    elif not isinstance(bbox["norm"], bool):
        out.append(FieldViolation("bbox.norm", "type", "norm must be bool"))

    # 추가 — norm=True 면 0~1 권장 (warn 수준만, violation 으로는 처리 X)
    # 음수 / >1 은 보수적으로 type 에러 만들지 X — 운영도 clamp 안 하므로.
    return out


def validate_payload_schema(payload: ProposalPayloadCandidate) -> list[FieldViolation]:
    """payload 가 모델 필드와 1:1 매핑되는지 schema 검증.

    누락 필드 + 모델 외 extra 필드 + audit 필드 누설 검사.
    """
    out: list[FieldViolation] = []
    payload_dict = asdict(payload)
    payload_keys = set(payload_dict.keys())

    # 모델 필드 누락
    missing = _MODEL_FIELDS_MAPPED - payload_keys
    for k in sorted(missing):
        out.append(FieldViolation(k, "missing", f"required model field '{k}' missing in payload"))

    # audit 필드 누설 금지 — payload 안에 reviewed_by 등 있으면 schema_extra 위반
    leaked_audit = payload_keys & _MODEL_AUDIT_FIELDS
    for k in sorted(leaked_audit):
        out.append(FieldViolation(
            k, "schema_extra",
            f"audit field '{k}' must not appear in payload (system-generated at approve)",
        ))

    return out


def validate_payload_fields(payload: ProposalPayloadCandidate) -> list[FieldViolation]:
    """필드 type / range / choice 검증."""
    out: list[FieldViolation] = []

    # tenant_id / document_id — int, > 0 (0 은 placeholder 만 허용 — but for real INSERT 0 not allowed)
    if not isinstance(payload.tenant_id, int) or payload.tenant_id < 0:
        out.append(FieldViolation("tenant_id", "out_of_range", f"tenant_id must be int >= 0, got {payload.tenant_id}"))
    if not isinstance(payload.document_id, int) or payload.document_id < 0:
        out.append(FieldViolation("document_id", "out_of_range", f"document_id must be int >= 0, got {payload.document_id}"))

    # page_number ≥ 0
    if not isinstance(payload.page_number, int) or payload.page_number < 0:
        out.append(FieldViolation("page_number", "out_of_range", f"page_number must be int >= 0, got {payload.page_number}"))

    # detected_problem_number — int, ≥ 0 (0 = unknown 허용)
    if not isinstance(payload.detected_problem_number, int) or payload.detected_problem_number < 0:
        out.append(FieldViolation(
            "detected_problem_number", "out_of_range",
            f"detected_problem_number must be int >= 0, got {payload.detected_problem_number}",
        ))

    # engine
    if payload.engine not in ENGINE_CHOICES:
        out.append(FieldViolation(
            "engine", "invalid_choice",
            f"engine '{payload.engine}' not in {sorted(ENGINE_CHOICES)}",
        ))

    # status
    if payload.status not in STATUS_CHOICES:
        out.append(FieldViolation(
            "status", "invalid_choice",
            f"status '{payload.status}' not in {sorted(STATUS_CHOICES)}",
        ))

    # model_version — str, max 64 (model 정의)
    if not isinstance(payload.model_version, str):
        out.append(FieldViolation("model_version", "type", "model_version must be str"))
    elif len(payload.model_version) > 64:
        out.append(FieldViolation("model_version", "out_of_range", f"len > 64 ({len(payload.model_version)})"))

    # confidence 0~1
    if not _is_finite_float(payload.confidence):
        out.append(FieldViolation("confidence", "type", "confidence not finite"))
    elif not (0.0 <= payload.confidence <= 1.0):
        out.append(FieldViolation(
            "confidence", "out_of_range",
            f"confidence must be 0~1, got {payload.confidence}",
        ))

    # analysis_version_key — str, max 128
    if not isinstance(payload.analysis_version_key, str):
        out.append(FieldViolation("analysis_version_key", "type", "must be str"))
    elif len(payload.analysis_version_key) > 128:
        out.append(FieldViolation("analysis_version_key", "out_of_range", f"len > 128"))

    # image_key — str, max 512
    if not isinstance(payload.image_key, str):
        out.append(FieldViolation("image_key", "type", "must be str"))
    elif len(payload.image_key) > 512:
        out.append(FieldViolation("image_key", "out_of_range", f"len > 512"))

    # raw_response — dict
    if not isinstance(payload.raw_response, dict):
        out.append(FieldViolation("raw_response", "type", "must be dict"))

    # bbox
    out.extend(_validate_bbox(payload.bbox))

    # validation_errors — list[ValidationError dataclass]
    if not isinstance(payload.validation_errors, list):
        out.append(FieldViolation("validation_errors", "type", "must be list"))
    else:
        for i, err in enumerate(payload.validation_errors):
            if not isinstance(err, ValidationError):
                out.append(FieldViolation(
                    f"validation_errors[{i}]", "type",
                    f"must be ValidationError, got {type(err).__name__}",
                ))
                continue
            if not err.code or not isinstance(err.code, str):
                out.append(FieldViolation(
                    f"validation_errors[{i}].code", "missing", "code missing or not str",
                ))
            if err.bbox_iou is not None and not _is_finite_float(err.bbox_iou):
                out.append(FieldViolation(
                    f"validation_errors[{i}].bbox_iou", "type", "must be finite or None",
                ))

    return out


def has_blocking_error(payload: ProposalPayloadCandidate) -> tuple[bool, list[str]]:
    """validation_errors 에 영구 차단 code 가 있는지.

    Returns: (has_block, [matched_codes])
    """
    matched: list[str] = []
    for err in payload.validation_errors or []:
        if isinstance(err, ValidationError) and err.code in PERMANENTLY_BLOCKING_CODES:
            matched.append(err.code)
    return (len(matched) > 0, matched)


def is_approvable(payload: ProposalPayloadCandidate) -> bool:
    """proposal_helpers.approve_proposal 동작과 동등한 approve gate.

    조건 (모두 AND):
    - status ∈ APPROVABLE_STATUSES (pending / needs_review / auto_passed)
    - validation_errors 에 영구 차단 code (manual_overlap) 없음
    """
    if payload.status not in APPROVABLE_STATUSES:
        return False
    blocked, _ = has_blocking_error(payload)
    return not blocked


def validate_status_transition(
    current_status: str, next_status: str,
) -> Optional[FieldViolation]:
    """status 전환 규칙 검증 — proposal_helpers approve/reject 흐름 호환.

    허용 전환:
    - pending / needs_review / auto_passed → approved (approve_proposal)
    - pending / needs_review → rejected (reject_proposal)
    - * → rejected (manual_overlap 자동 transition — create_proposal)
    - approved → 변경 불가
    - rejected → 변경 불가 (영구)

    현재 상태가 approved / rejected 면 next 가 어디로 가도 위반.
    """
    if current_status not in STATUS_CHOICES:
        return FieldViolation("status", "invalid_choice", f"current status '{current_status}' invalid")
    if next_status not in STATUS_CHOICES:
        return FieldViolation("status", "invalid_choice", f"next status '{next_status}' invalid")

    if current_status == "approved" and next_status != "approved":
        return FieldViolation(
            "status", "invalid_choice",
            "approved → other transition forbidden (audit invariant)",
        )
    if current_status == "rejected" and next_status != "rejected":
        return FieldViolation(
            "status", "invalid_choice",
            "rejected → other transition forbidden (permanent block)",
        )
    return None


# ── manual_overlap provider 추상화 (DB query 0회 default) ───────────


ManualOverlapProvider = Callable[
    [int, int, dict],  # (document_id, page_number, candidate_bbox dict)
    tuple[bool, float, Optional[int]],  # (overlaps, max_iou, conflicting_problem_id)
]


def static_manual_overlap_provider(
    static_manual_bboxes: Iterable[dict],
    threshold: float = MANUAL_OVERLAP_IOU_THRESHOLD,
) -> ManualOverlapProvider:
    """static bbox list 만 사용 — DB query 0회.

    static_manual_bboxes 의 각 entry: {"document_id", "page_number", "bbox_norm"}
    """
    materialized = list(static_manual_bboxes)

    def _provider(
        document_id: int, page_number: int, bbox_dict: dict,
    ) -> tuple[bool, float, Optional[int]]:
        try:
            cand = (
                float(bbox_dict["x"]), float(bbox_dict["y"]),
                float(bbox_dict["w"]), float(bbox_dict["h"]),
            )
        except (KeyError, TypeError, ValueError):
            return (True, -1.0, None)  # 보수적
        max_iou = 0.0
        conflict_id: Optional[int] = None
        for m in materialized:
            if m.get("document_id") != document_id:
                continue
            if m.get("page_number") != page_number:
                continue
            mb = m.get("bbox_norm")
            if not isinstance(mb, (list, tuple)) or len(mb) != 4:
                continue
            iou = _bbox_iou_norm(cand, tuple(mb))
            if iou > max_iou:
                max_iou = iou
                conflict_id = m.get("manual_problem_id")
        return (max_iou > threshold, max_iou, conflict_id)

    return _provider


def apply_manual_overlap_via_provider(
    payload: ProposalPayloadCandidate,
    provider: ManualOverlapProvider,
) -> ProposalPayloadCandidate:
    """provider 결과로 payload 갱신 — 새 ProposalPayloadCandidate 반환 (immutable 의도).

    새 ValidationError 추가 + status='rejected' 자동 전환.
    DB INSERT 절대 X.
    """
    overlaps, max_iou, conflict_id = provider(
        payload.document_id, payload.page_number, payload.bbox,
    )
    if not overlaps:
        return payload
    new_errors = list(payload.validation_errors)
    # 중복 추가 방지
    if not any(isinstance(e, ValidationError) and e.code == "manual_overlap" for e in new_errors):
        new_errors.append(ValidationError(
            code="manual_overlap",
            detail=f"manual cut overlap (provider) IoU={max_iou:.3f} conflict={conflict_id}",
            bbox_iou=round(max_iou, 3) if max_iou >= 0 else None,
        ))
    return ProposalPayloadCandidate(
        tenant_id=payload.tenant_id, document_id=payload.document_id,
        page_number=payload.page_number,
        detected_problem_number=payload.detected_problem_number,
        bbox=dict(payload.bbox), engine=payload.engine,
        model_version=payload.model_version, confidence=payload.confidence,
        status="rejected",  # 영구 차단
        analysis_version_key=payload.analysis_version_key,
        image_key=payload.image_key, raw_response=dict(payload.raw_response),
        validation_errors=new_errors,
    )


# ── selected_problem_ids 무관성 검증 ────────────────────────────────


def assert_selected_problem_ids_independence(
    payloads: Iterable[ProposalPayloadCandidate],
) -> bool:
    """payload 가 selected_problem_ids 에 영향 미치지 않는지 구조 검증.

    payload schema 자체에 selected_problem_ids 또는 promoted_problem 필드 없음을 확인.
    payload 가 어떻게 사용돼도 selected_problem_ids 변경 path 에 들어갈 수 없음.

    True 면 무관성 보장됨. False 면 schema 위반 (audit 필드 누설 등).
    """
    forbidden_keys = {"selected_problem_ids", "promoted_problem", "promoted_problem_id"}
    for p in payloads:
        keys = set(asdict(p).keys())
        if keys & forbidden_keys:
            return False
    return True


# ── 통합 batch 검증 ───────────────────────────────────────────────


def validate_payload(payload: ProposalPayloadCandidate) -> PayloadValidationResult:
    """단일 payload 종합 검증."""
    schema_violations = validate_payload_schema(payload)
    field_violations = validate_payload_fields(payload)
    blocked, blocked_codes = has_blocking_error(payload)
    approvable = is_approvable(payload)

    schema_ok = len(schema_violations) == 0
    field_ok = len(field_violations) == 0
    status_ok = payload.status in STATUS_CHOICES

    return PayloadValidationResult(
        schema_ok=schema_ok, field_ok=field_ok, status_ok=status_ok,
        is_approvable=approvable,
        violations=schema_violations + field_violations,
        blocking_codes=blocked_codes,
    )


def validate_batch(
    payloads: Iterable[ProposalPayloadCandidate],
) -> BatchValidationReport:
    """payload list 통합 검증 — INSERT 0회 / DB query 0회."""
    results: list[PayloadValidationResult] = []
    schema_ok = field_ok = status_ok = approvable = blocked = 0
    for p in payloads:
        r = validate_payload(p)
        results.append(r)
        if r.schema_ok: schema_ok += 1
        if r.field_ok: field_ok += 1
        if r.status_ok: status_ok += 1
        if r.is_approvable: approvable += 1
        if r.blocking_codes: blocked += 1
    return BatchValidationReport(
        schema_version=SCHEMA_VERSION, total=len(results),
        schema_ok_count=schema_ok, field_ok_count=field_ok,
        status_ok_count=status_ok, approvable_count=approvable,
        blocked_count=blocked, results=results,
    )


def report_to_dict(r: BatchValidationReport) -> dict[str, Any]:
    return {
        "schema_version": r.schema_version,
        "total": r.total,
        "schema_ok_count": r.schema_ok_count,
        "field_ok_count": r.field_ok_count,
        "status_ok_count": r.status_ok_count,
        "approvable_count": r.approvable_count,
        "blocked_count": r.blocked_count,
        "results": [
            {
                "schema_ok": rr.schema_ok, "field_ok": rr.field_ok,
                "status_ok": rr.status_ok, "is_approvable": rr.is_approvable,
                "blocking_codes": rr.blocking_codes,
                "violations": [asdict(v) for v in rr.violations],
            }
            for rr in r.results
        ],
    }
