# PATH: apps/domains/students/services/bulk_from_excel.py
# 엑셀 파싱된 행으로 학생만 일괄 생성 (수강 등록 없음). 워커 전용.

from __future__ import annotations

import logging
from typing import Callable

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from .lecture_enroll import get_or_create_student_for_lecture_enroll

logger = logging.getLogger(__name__)


def bulk_create_students_from_excel_rows(
    *,
    tenant_id: int,
    students_data: list[dict],
    initial_password: str,
    on_row_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """
    엑셀 파싱된 행으로 학생만 일괄 생성. 수강 등록은 하지 않음.
    워커 excel_parsing (lecture_id 없을 때) 전용.
    Returns: { "created": int, "failed": [{ "row", "name", "error", "conflict_student_id"? }], "total": int }
    """
    tenant = enroll_repo.get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("tenant_id not found")

    initial_password = (initial_password or "").strip()
    if len(initial_password) < 4:
        raise ValueError("initial_password는 4자 이상이어야 합니다.")

    created_count = 0
    failed: list[dict] = []
    total = len(students_data)

    for row_index, raw in enumerate(students_data, start=1):
        if on_row_progress and total > 0:
            on_row_progress(row_index, total)
        item = dict(raw) if isinstance(raw, dict) else {}
        name = (item.get("name") or "").strip()
        parent_phone = (item.get("parent_phone") or item.get("parentPhone") or "")
        parent_phone = "".join(c for c in str(parent_phone) if c.isdigit())

        if not name and not parent_phone:
            continue

        try:
            student, created = get_or_create_student_for_lecture_enroll(
                tenant, item, initial_password
            )
            if student and created:
                created_count += 1
            elif not student:
                failed.append({
                    "row": row_index,
                    "name": name or "(이름 없음)",
                    "error": "이름·학부모전화(010 11자리) 조건 미충족 또는 이미 존재하는 학생입니다.",
                    "conflict_student_id": None,
                })
        except ValueError as e:
            err_msg = str(e.args[0]) if e.args else str(e)
            conflict_sid = e.args[1] if len(e.args) >= 2 else None
            failed.append({
                "row": row_index,
                "name": name or "(이름 없음)",
                "error": err_msg,
                "conflict_student_id": conflict_sid,
            })
        except Exception as e:
            logger.warning(
                "bulk_create_students_from_excel row=%s name=%r: %s",
                row_index,
                name,
                e,
                exc_info=True,
            )
            failed.append({
                "row": row_index,
                "name": name or "(이름 없음)",
                "error": str(e)[:500],
                "conflict_student_id": None,
            })

    if not created_count and not failed and total > 0:
        raise ValueError(
            "등록할 수 있는 학생이 없습니다. 이름·학부모 전화번호(010 11자리)를 확인해 주세요."
        )

    return {
        "created": created_count,
        "failed": failed,
        "total": total,
        "processed_by": "worker",
    }
