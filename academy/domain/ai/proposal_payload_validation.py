"""Pure validation for AI segmentation proposal payload contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Optional

from academy.domain.ai.segmentation_contracts import (
    ProposalPayloadCandidate,
    ValidationError,
    _bbox_iou_norm,
)


SCHEMA_VERSION = "5.9-validator-1"

ENGINE_CHOICES = frozenset({"yolo", "vlm", "ocr", "native_pdf", "manual_assist"})
STATUS_CHOICES = frozenset({"pending", "needs_review", "rejected", "approved", "auto_passed"})
APPROVABLE_STATUSES = frozenset({"pending", "needs_review", "auto_passed"})
MANUAL_OVERLAP_IOU_THRESHOLD = 0.30
PERMANENTLY_BLOCKING_CODES = frozenset({"manual_overlap"})

_MODEL_FIELDS_MAPPED = frozenset({
    "tenant_id", "document_id", "analysis_version_key",
    "page_number", "bbox", "detected_problem_number",
    "engine", "model_version", "confidence",
    "status", "image_key", "raw_response", "validation_errors",
})
_MODEL_AUDIT_FIELDS = frozenset({
    "reviewed_by", "reviewed_by_id", "reviewed_at",
    "promoted_problem", "promoted_problem_id",
})


@dataclass
class FieldViolation:
    field: str
    code: str
    detail: str


@dataclass
class PayloadValidationResult:
    schema_ok: bool
    field_ok: bool
    status_ok: bool
    is_approvable: bool
    violations: list[FieldViolation] = field(default_factory=list)
    blocking_codes: list[str] = field(default_factory=list)


@dataclass
class BatchValidationReport:
    schema_version: str
    total: int
    schema_ok_count: int
    field_ok_count: int
    status_ok_count: int
    approvable_count: int
    blocked_count: int
    results: list[PayloadValidationResult] = field(default_factory=list)


def _is_finite_float(v: Any) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    return v == v and v not in (float("inf"), float("-inf"))


def _validate_bbox(bbox: Any) -> list[FieldViolation]:
    out: list[FieldViolation] = []
    if not isinstance(bbox, dict):
        return [FieldViolation("bbox", "type", f"bbox must be dict, got {type(bbox).__name__}")]
    for key in ("x", "y", "w", "h"):
        if key not in bbox:
            out.append(FieldViolation(f"bbox.{key}", "missing", f"missing key {key}"))
        elif not _is_finite_float(bbox[key]):
            out.append(FieldViolation(f"bbox.{key}", "type", f"{key} not finite number"))
    if "norm" not in bbox:
        out.append(FieldViolation("bbox.norm", "missing", "missing key 'norm'"))
    elif not isinstance(bbox["norm"], bool):
        out.append(FieldViolation("bbox.norm", "type", "norm must be bool"))
    return out


def validate_payload_schema(payload: ProposalPayloadCandidate) -> list[FieldViolation]:
    out: list[FieldViolation] = []
    payload_keys = set(asdict(payload).keys())
    for key in sorted(_MODEL_FIELDS_MAPPED - payload_keys):
        out.append(FieldViolation(key, "missing", f"required model field '{key}' missing in payload"))
    for key in sorted(payload_keys & _MODEL_AUDIT_FIELDS):
        out.append(
            FieldViolation(
                key,
                "schema_extra",
                f"audit field '{key}' must not appear in payload (system-generated at approve)",
            )
        )
    return out


def validate_payload_fields(payload: ProposalPayloadCandidate) -> list[FieldViolation]:
    out: list[FieldViolation] = []
    if not isinstance(payload.tenant_id, int) or payload.tenant_id < 0:
        out.append(FieldViolation("tenant_id", "out_of_range", f"tenant_id must be int >= 0, got {payload.tenant_id}"))
    if not isinstance(payload.document_id, int) or payload.document_id < 0:
        out.append(FieldViolation("document_id", "out_of_range", f"document_id must be int >= 0, got {payload.document_id}"))
    if not isinstance(payload.page_number, int) or payload.page_number < 0:
        out.append(FieldViolation("page_number", "out_of_range", f"page_number must be int >= 0, got {payload.page_number}"))
    if not isinstance(payload.detected_problem_number, int) or payload.detected_problem_number < 0:
        out.append(
            FieldViolation(
                "detected_problem_number",
                "out_of_range",
                f"detected_problem_number must be int >= 0, got {payload.detected_problem_number}",
            )
        )
    if payload.engine not in ENGINE_CHOICES:
        out.append(FieldViolation("engine", "invalid_choice", f"engine '{payload.engine}' not in {sorted(ENGINE_CHOICES)}"))
    if payload.status not in STATUS_CHOICES:
        out.append(FieldViolation("status", "invalid_choice", f"status '{payload.status}' not in {sorted(STATUS_CHOICES)}"))
    if not isinstance(payload.model_version, str):
        out.append(FieldViolation("model_version", "type", "model_version must be str"))
    elif len(payload.model_version) > 64:
        out.append(FieldViolation("model_version", "out_of_range", f"len > 64 ({len(payload.model_version)})"))
    if not _is_finite_float(payload.confidence):
        out.append(FieldViolation("confidence", "type", "confidence not finite"))
    elif not (0.0 <= payload.confidence <= 1.0):
        out.append(FieldViolation("confidence", "out_of_range", f"confidence must be 0~1, got {payload.confidence}"))
    if not isinstance(payload.analysis_version_key, str):
        out.append(FieldViolation("analysis_version_key", "type", "must be str"))
    elif len(payload.analysis_version_key) > 128:
        out.append(FieldViolation("analysis_version_key", "out_of_range", "len > 128"))
    if not isinstance(payload.image_key, str):
        out.append(FieldViolation("image_key", "type", "must be str"))
    elif len(payload.image_key) > 512:
        out.append(FieldViolation("image_key", "out_of_range", "len > 512"))
    if not isinstance(payload.raw_response, dict):
        out.append(FieldViolation("raw_response", "type", "must be dict"))

    out.extend(_validate_bbox(payload.bbox))

    if not isinstance(payload.validation_errors, list):
        out.append(FieldViolation("validation_errors", "type", "must be list"))
    else:
        for idx, err in enumerate(payload.validation_errors):
            if not isinstance(err, ValidationError):
                out.append(FieldViolation(f"validation_errors[{idx}]", "type", f"must be ValidationError, got {type(err).__name__}"))
                continue
            if not err.code or not isinstance(err.code, str):
                out.append(FieldViolation(f"validation_errors[{idx}].code", "missing", "code missing or not str"))
            if err.bbox_iou is not None and not _is_finite_float(err.bbox_iou):
                out.append(FieldViolation(f"validation_errors[{idx}].bbox_iou", "type", "must be finite or None"))
    return out


def has_blocking_error(payload: ProposalPayloadCandidate) -> tuple[bool, list[str]]:
    matched = [
        err.code
        for err in payload.validation_errors or []
        if isinstance(err, ValidationError) and err.code in PERMANENTLY_BLOCKING_CODES
    ]
    return (len(matched) > 0, matched)


def is_approvable(payload: ProposalPayloadCandidate) -> bool:
    if payload.status not in APPROVABLE_STATUSES:
        return False
    blocked, _ = has_blocking_error(payload)
    return not blocked


def validate_status_transition(current_status: str, next_status: str) -> Optional[FieldViolation]:
    if current_status not in STATUS_CHOICES:
        return FieldViolation("status", "invalid_choice", f"current status '{current_status}' invalid")
    if next_status not in STATUS_CHOICES:
        return FieldViolation("status", "invalid_choice", f"next status '{next_status}' invalid")
    if current_status == "approved" and next_status != "approved":
        return FieldViolation("status", "invalid_choice", "approved -> other transition forbidden (audit invariant)")
    if current_status == "rejected" and next_status != "rejected":
        return FieldViolation("status", "invalid_choice", "rejected -> other transition forbidden (permanent block)")
    return None


ManualOverlapProvider = Callable[
    [int, int, dict],
    tuple[bool, float, Optional[int]],
]


def static_manual_overlap_provider(
    static_manual_bboxes: Iterable[dict],
    threshold: float = MANUAL_OVERLAP_IOU_THRESHOLD,
) -> ManualOverlapProvider:
    materialized = list(static_manual_bboxes)

    def _provider(document_id: int, page_number: int, bbox_dict: dict) -> tuple[bool, float, Optional[int]]:
        try:
            cand = (
                float(bbox_dict["x"]),
                float(bbox_dict["y"]),
                float(bbox_dict["w"]),
                float(bbox_dict["h"]),
            )
        except (KeyError, TypeError, ValueError):
            return (True, -1.0, None)
        max_iou = 0.0
        conflict_id: Optional[int] = None
        for item in materialized:
            if item.get("document_id") != document_id or item.get("page_number") != page_number:
                continue
            manual_bbox = item.get("bbox_norm")
            if not isinstance(manual_bbox, (list, tuple)) or len(manual_bbox) != 4:
                continue
            iou = _bbox_iou_norm(cand, tuple(manual_bbox))
            if iou > max_iou:
                max_iou = iou
                conflict_id = item.get("manual_problem_id")
        return (max_iou > threshold, max_iou, conflict_id)

    return _provider


def apply_manual_overlap_via_provider(
    payload: ProposalPayloadCandidate,
    provider: ManualOverlapProvider,
) -> ProposalPayloadCandidate:
    overlaps, max_iou, conflict_id = provider(payload.document_id, payload.page_number, payload.bbox)
    if not overlaps:
        return payload
    new_errors = list(payload.validation_errors)
    if not any(isinstance(err, ValidationError) and err.code == "manual_overlap" for err in new_errors):
        new_errors.append(
            ValidationError(
                code="manual_overlap",
                detail=f"manual cut overlap (provider) IoU={max_iou:.3f} conflict={conflict_id}",
                bbox_iou=round(max_iou, 3) if max_iou >= 0 else None,
            )
        )
    return ProposalPayloadCandidate(
        tenant_id=payload.tenant_id,
        document_id=payload.document_id,
        page_number=payload.page_number,
        detected_problem_number=payload.detected_problem_number,
        bbox=dict(payload.bbox),
        engine=payload.engine,
        model_version=payload.model_version,
        confidence=payload.confidence,
        status="rejected",
        analysis_version_key=payload.analysis_version_key,
        image_key=payload.image_key,
        raw_response=dict(payload.raw_response),
        validation_errors=new_errors,
    )


def assert_selected_problem_ids_independence(
    payloads: Iterable[ProposalPayloadCandidate],
) -> bool:
    forbidden_keys = {"selected_problem_ids", "promoted_problem", "promoted_problem_id"}
    for payload in payloads:
        if set(asdict(payload).keys()) & forbidden_keys:
            return False
    return True


def validate_payload(payload: ProposalPayloadCandidate) -> PayloadValidationResult:
    schema_violations = validate_payload_schema(payload)
    field_violations = validate_payload_fields(payload)
    blocked, blocked_codes = has_blocking_error(payload)
    return PayloadValidationResult(
        schema_ok=len(schema_violations) == 0,
        field_ok=len(field_violations) == 0,
        status_ok=payload.status in STATUS_CHOICES,
        is_approvable=is_approvable(payload),
        violations=schema_violations + field_violations,
        blocking_codes=blocked_codes,
    )


def validate_batch(payloads: Iterable[ProposalPayloadCandidate]) -> BatchValidationReport:
    results: list[PayloadValidationResult] = []
    schema_ok = field_ok = status_ok = approvable = blocked = 0
    for payload in payloads:
        result = validate_payload(payload)
        results.append(result)
        if result.schema_ok:
            schema_ok += 1
        if result.field_ok:
            field_ok += 1
        if result.status_ok:
            status_ok += 1
        if result.is_approvable:
            approvable += 1
        if result.blocking_codes:
            blocked += 1
    return BatchValidationReport(
        schema_version=SCHEMA_VERSION,
        total=len(results),
        schema_ok_count=schema_ok,
        field_ok_count=field_ok,
        status_ok_count=status_ok,
        approvable_count=approvable,
        blocked_count=blocked,
        results=results,
    )


def report_to_dict(report: BatchValidationReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "total": report.total,
        "schema_ok_count": report.schema_ok_count,
        "field_ok_count": report.field_ok_count,
        "status_ok_count": report.status_ok_count,
        "approvable_count": report.approvable_count,
        "blocked_count": report.blocked_count,
        "results": [
            {
                "schema_ok": result.schema_ok,
                "field_ok": result.field_ok,
                "status_ok": result.status_ok,
                "is_approvable": result.is_approvable,
                "blocking_codes": result.blocking_codes,
                "violations": [asdict(violation) for violation in result.violations],
            }
            for result in report.results
        ],
    }
