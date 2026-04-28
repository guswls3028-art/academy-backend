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
    created_students: list = []
    failed: list[dict] = []
    duplicates: list[dict] = []
    restored: list[dict] = []
    total = len(students_data)
    skipped_empty = 0

    for row_index, raw in enumerate(students_data, start=1):
        if on_row_progress and total > 0:
            on_row_progress(row_index, total)
        item = dict(raw) if isinstance(raw, dict) else {}
        name = (item.get("name") or "").strip()
        parent_phone = (item.get("parent_phone") or item.get("parentPhone") or "")
        parent_phone = "".join(c for c in str(parent_phone) if c.isdigit())

        if not name and not parent_phone:
            skipped_empty += 1
            continue

        try:
            student, created, was_restored = get_or_create_student_for_lecture_enroll(
                tenant, item, initial_password
            )
            if student and created:
                created_count += 1
                created_students.append(student)
            elif student and was_restored:
                restored.append({
                    "row": row_index,
                    "name": name or "(이름 없음)",
                    "student_id": student.id,
                })
            elif student and not created:
                # 이미 활성 상태로 존재하는 학생
                duplicates.append({
                    "row": row_index,
                    "name": name or "(이름 없음)",
                    "student_id": student.id,
                })
            elif not student:
                failed.append({
                    "row": row_index,
                    "name": name or "(이름 없음)",
                    "error": "이름·학부모전화(010 11자리) 조건 미충족",
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

    # 가입 안내 알림톡 발송 (새로 생성된 학생만)
    if created_students:
        try:
            from apps.domains.messaging.services import send_welcome_messages, get_tenant_site_url
            site_url = get_tenant_site_url(tenant)
            parent_pw = {
                s.parent_phone: "0000"
                for s in created_students
                if getattr(s, "parent_phone", None)
            }
            send_welcome_messages(
                created_students=created_students,
                student_password=initial_password,
                parent_password_by_phone=parent_pw,
                site_url=site_url,
            )
        except Exception:
            logger.exception("bulk_create_excel: send_welcome_messages failed (non-fatal)")

    if not created_count and not failed and not duplicates and not restored and total > 0:
        logger.error(
            "[bulk_create_excel] ALL students skipped: total=%s skipped_empty=%s",
            total, skipped_empty,
        )
        raise ValueError(
            f"등록할 수 있는 학생이 없습니다. "
            f"전체 {total}행 중 {skipped_empty}행이 이름·전화 모두 비어 건너뜀. "
            f"이름·학부모 전화번호(010 11자리)를 확인해 주세요."
        )

    return {
        "created": created_count,
        "failed": failed,
        "duplicates": duplicates,
        "restored": restored,
        "total": total,
        "processed_by": "worker",
    }
