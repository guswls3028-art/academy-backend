"""Compatibility facade for AI segmentation proposal payload validation.

The canonical pure validation contract lives in
`academy.domain.ai.proposal_payload_validation` so adapters do not depend on
application use cases.
"""
from academy.domain.ai.proposal_payload_validation import (
    APPROVABLE_STATUSES,
    ENGINE_CHOICES,
    MANUAL_OVERLAP_IOU_THRESHOLD,
    PERMANENTLY_BLOCKING_CODES,
    SCHEMA_VERSION,
    STATUS_CHOICES,
    BatchValidationReport,
    FieldViolation,
    ManualOverlapProvider,
    PayloadValidationResult,
    apply_manual_overlap_via_provider,
    assert_selected_problem_ids_independence,
    has_blocking_error,
    is_approvable,
    report_to_dict,
    static_manual_overlap_provider,
    validate_batch,
    validate_payload,
    validate_payload_fields,
    validate_payload_schema,
    validate_status_transition,
)

__all__ = [
    "APPROVABLE_STATUSES",
    "ENGINE_CHOICES",
    "MANUAL_OVERLAP_IOU_THRESHOLD",
    "PERMANENTLY_BLOCKING_CODES",
    "SCHEMA_VERSION",
    "STATUS_CHOICES",
    "BatchValidationReport",
    "FieldViolation",
    "ManualOverlapProvider",
    "PayloadValidationResult",
    "apply_manual_overlap_via_provider",
    "assert_selected_problem_ids_independence",
    "has_blocking_error",
    "is_approvable",
    "report_to_dict",
    "static_manual_overlap_provider",
    "validate_batch",
    "validate_payload",
    "validate_payload_fields",
    "validate_payload_schema",
    "validate_status_transition",
]
