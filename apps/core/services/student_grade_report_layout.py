"""Tenant-owned layout policy for the student growth report."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.core.models import Program


STUDENT_GRADE_REPORT_LAYOUT_KEY = "student_grade_report_layout"
STUDENT_GRADE_REPORT_LAYOUT_VERSION = 2
STUDENT_GRADE_REPORT_SECTION_IDS = (
    "score_trend",
    "score_comparison",
    "lecture_average",
    "improvement_priority",
    "exam_summary",
    "rank_position",
    "weakest_lecture",
    "homework_summary",
)
STUDENT_GRADE_REPORT_SCORE_COMPARISON_METRIC_IDS = (
    "average_score",
    "pass_rate",
    "status",
)


def default_student_grade_report_layout() -> dict[str, Any]:
    return {
        "version": STUDENT_GRADE_REPORT_LAYOUT_VERSION,
        "sections": [
            {"id": section_id, "visible": True}
            for section_id in STUDENT_GRADE_REPORT_SECTION_IDS
        ],
        "score_comparison_metrics": {
            metric_id: True
            for metric_id in STUDENT_GRADE_REPORT_SCORE_COMPARISON_METRIC_IDS
        },
    }


def ymath_student_grade_report_layout() -> dict[str, Any]:
    """Initial YMath preference; it remains editable through the normal API."""
    hidden = {
        "improvement_priority",
        "exam_summary",
        "rank_position",
        "weakest_lecture",
        "homework_summary",
    }
    layout = default_student_grade_report_layout()
    for section in layout["sections"]:
        section["visible"] = section["id"] not in hidden
    layout["score_comparison_metrics"]["pass_rate"] = False
    layout["score_comparison_metrics"]["status"] = False
    return layout


def normalize_student_grade_report_layout(value: Any) -> dict[str, Any]:
    """Return a complete forward-compatible layout without mutating storage."""
    defaults = default_student_grade_report_layout()
    if not isinstance(value, dict) or not isinstance(value.get("sections"), list):
        return defaults

    known = set(STUDENT_GRADE_REPORT_SECTION_IDS)
    seen: set[str] = set()
    sections: list[dict[str, Any]] = []
    for raw in value["sections"]:
        if not isinstance(raw, dict):
            continue
        section_id = raw.get("id")
        if section_id not in known or section_id in seen:
            continue
        seen.add(section_id)
        sections.append({
            "id": section_id,
            "visible": raw.get("visible") is not False,
        })

    for section_id in STUDENT_GRADE_REPORT_SECTION_IDS:
        if section_id not in seen:
            sections.append({"id": section_id, "visible": True})

    metrics = dict(defaults["score_comparison_metrics"])
    raw_metrics = value.get("score_comparison_metrics")
    if isinstance(raw_metrics, dict):
        for metric_id in STUDENT_GRADE_REPORT_SCORE_COMPARISON_METRIC_IDS:
            metrics[metric_id] = raw_metrics.get(metric_id) is not False

    return {
        "version": STUDENT_GRADE_REPORT_LAYOUT_VERSION,
        "sections": sections,
        "score_comparison_metrics": metrics,
    }


def validate_student_grade_report_layout(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("sections"), list):
        raise ValidationError({"sections": "성적표 섹션 목록이 필요합니다."})

    sections = value["sections"]
    expected = set(STUDENT_GRADE_REPORT_SECTION_IDS)
    ids: list[str] = []
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(sections):
        if not isinstance(raw, dict):
            raise ValidationError({"sections": f"{index + 1}번째 섹션 형식이 올바르지 않습니다."})
        section_id = raw.get("id")
        visible = raw.get("visible")
        if section_id not in expected:
            raise ValidationError({"sections": f"지원하지 않는 섹션입니다: {section_id}"})
        if section_id in ids:
            raise ValidationError({"sections": f"중복된 섹션입니다: {section_id}"})
        if not isinstance(visible, bool):
            raise ValidationError({"sections": f"{section_id}의 표시 여부는 true/false여야 합니다."})
        ids.append(section_id)
        normalized.append({"id": section_id, "visible": visible})

    if set(ids) != expected or len(ids) != len(expected):
        missing = [section_id for section_id in STUDENT_GRADE_REPORT_SECTION_IDS if section_id not in ids]
        raise ValidationError({"sections": f"모든 성적표 섹션을 포함해야 합니다: {', '.join(missing)}"})
    if not any(section["visible"] for section in normalized):
        raise ValidationError({"sections": "학생에게 표시할 섹션을 하나 이상 선택해 주세요."})

    raw_metrics = value.get("score_comparison_metrics")
    expected_metrics = set(STUDENT_GRADE_REPORT_SCORE_COMPARISON_METRIC_IDS)
    if not isinstance(raw_metrics, dict) or set(raw_metrics) != expected_metrics:
        raise ValidationError(
            {
                "score_comparison_metrics": (
                    "성적 비교의 평균 득점률, 통과율, 상태 표시 여부를 모두 포함해 주세요."
                )
            }
        )
    if any(not isinstance(raw_metrics[metric_id], bool) for metric_id in expected_metrics):
        raise ValidationError(
            {"score_comparison_metrics": "각 성적 비교 항목의 표시 여부는 true/false여야 합니다."}
        )

    return {
        "version": STUDENT_GRADE_REPORT_LAYOUT_VERSION,
        "sections": normalized,
        "score_comparison_metrics": {
            metric_id: raw_metrics[metric_id]
            for metric_id in STUDENT_GRADE_REPORT_SCORE_COMPARISON_METRIC_IDS
        },
    }


def get_student_grade_report_layout(*, tenant: Any) -> dict[str, Any]:
    try:
        program = tenant.program
    except Program.DoesNotExist:
        return default_student_grade_report_layout()
    ui_config = program.ui_config if isinstance(program.ui_config, dict) else {}
    return normalize_student_grade_report_layout(
        ui_config.get(STUDENT_GRADE_REPORT_LAYOUT_KEY)
    )


@transaction.atomic
def save_student_grade_report_layout(*, tenant: Any, value: Any) -> dict[str, Any]:
    try:
        program = Program.objects.select_for_update().get(tenant=tenant)
    except Program.DoesNotExist as exc:
        raise ValidationError({"detail": "이 학원의 프로그램 설정을 찾을 수 없습니다."}) from exc

    ui_config = dict(program.ui_config) if isinstance(program.ui_config, dict) else {}
    payload = dict(value) if isinstance(value, dict) else value
    if isinstance(payload, dict) and "score_comparison_metrics" not in payload:
        current = normalize_student_grade_report_layout(
            ui_config.get(STUDENT_GRADE_REPORT_LAYOUT_KEY)
        )
        payload["score_comparison_metrics"] = current["score_comparison_metrics"]
    layout = validate_student_grade_report_layout(payload)
    ui_config[STUDENT_GRADE_REPORT_LAYOUT_KEY] = layout
    program.ui_config = ui_config
    program.save(update_fields=["ui_config", "updated_at"])
    return layout
