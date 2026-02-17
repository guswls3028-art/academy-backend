# PATH: src/application/services/excel_parsing_service.py
# 비즈니스 로직의 핵심: 엑셀 파싱 + 강의 수강 등록 원테이크 (헥사고날 Application Service)
# - 엑셀 파싱·등록 흐름은 이 서비스만 통과 (워커/API 공통)
# - "무적의 엑셀 파싱": 헤더 별칭 매칭, 이름·학부모전화 기준 행 파싱

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from src.application.ports.storage import IObjectStorage

logger = logging.getLogger(__name__)

# 헤더 별칭 (학원별 양식 대응 — 양식 안 맞춰도 인식되도록 넓게)
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("이름", "성명", "학생명", "학생 이름", "이름(학생)", "성함", "학생성명"),
    "parent_phone": (
        "학부모전화번호", "부모핸드폰", "부모 전화", "학부모 전화", "보호자 전화", "보호자전화",
        "학부모연락처", "부모 연락처", "보호자 연락처", "연락처(학부모)", "전화(학부모)",
        "휴대폰", "핸드폰", "연락처", "전화번호", "전화",
    ),
    "student_phone": (
        "학생전화번호", "학생핸드폰", "학생 전화", "학생연락처", "학생 연락처",
        "연락처(학생)", "전화(학생)",
    ),
    "school": ("학교", "학교(학년)", "학교명", "출신학교"),
    "grade": ("학년", "학년도"),
    "gender": ("성별",),
    "school_class": ("반", "학급"),
    "major": ("계열",),
    "memo": ("메모", "비고", "특이사항"),
    "remark": ("구분", "체크", "비고", "비고2"),
}


def _normalize_header(label: str) -> str:
    return re.sub(r"\s", "", (label or "").strip().lower())


def _match_header(cell: str, key: str) -> bool:
    norm = _normalize_header(cell)
    for alias in HEADER_ALIASES.get(key, ()):
        if _normalize_header(alias) == norm:
            return True
    return False


def _find_header_row(rows: list[list[Any]]) -> int:
    """이름 + 전화 컬럼이 모두 있는 첫 행을 헤더로 반환."""
    for i, row in enumerate(rows):
        if not row:
            continue
        has_name = any(_match_header(str(c), "name") for c in row)
        has_phone = any(
            _match_header(str(c), "parent_phone") or _match_header(str(c), "student_phone")
            for c in row
        )
        if has_name and has_phone:
            return i
    return -1


def _find_header_row_fallback(rows: list[list[Any]]) -> int:
    """표준 헤더를 못 찾았을 때: 첫 번째 비어 있지 않은 행을 헤더로 시도 (이름 또는 전화만 있어도 인식)."""
    for i, row in enumerate(rows[:10]):  # 상위 10행만 후보
        if not row:
            continue
        col = _build_header_map(row)
        if col.get("name") is not None or col.get("parent_phone") is not None or col.get("student_phone") is not None:
            return i
    return -1


def _build_header_map(header_row: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        label = str(cell or "").strip()
        if not label:
            continue
        for key in HEADER_ALIASES:
            if key in out:
                continue
            if _match_header(label, key):
                out[key] = i
                break
    return out


def _cell_str(row: list[Any], col_index: int | None) -> str:
    if col_index is None or col_index >= len(row):
        return ""
    return str(row[col_index] or "").strip()


def _to_raw_phone(v: str) -> str:
    return re.sub(r"\D", "", v)


def _parse_school_grade(value: str) -> tuple[str, str]:
    value = (value or "").strip()
    if not value:
        return "", ""
    m = re.match(r"^(.+?)\(([０-９0-9]+)\)\s*$", value)
    if not m:
        return value, ""
    school = m.group(1).strip()
    grade = m.group(2)
    grade = "".join(
        chr(ord(c) - 0xFEE0) if "\uFF10" <= c <= "\uFF19" else c for c in grade
    )
    grade = re.sub(r"\D", "", grade)
    return school, grade


def _infer_school_type(school: str) -> str:
    if not school:
        return "HIGH"
    if re.search(r"중학교|중등|중\b", school):
        return "MIDDLE"
    return "HIGH"


def parse_student_excel_file(local_path: str) -> list[dict[str, Any]]:
    """
    로컬 엑셀 파일을 파싱하여 강의 수강 등록용 행 리스트 반환.
    양식 안 맞춰도 인식: 헤더 별칭 넓게 지원, 이름+전화 없으면 첫 행 기준으로도 시도.
    """
    import openpyxl

    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {local_path}")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    if not ws:
        wb.close()
        raise ValueError("No active sheet")

    rows: list[list[Any]] = []
    for row in ws.iter_rows(values_only=True):
        rows.append(list(row) if row else [])

    wb.close()

    if not rows:
        return []

    header_idx = _find_header_row(rows)
    used_fallback = False
    if header_idx < 0:
        header_idx = _find_header_row_fallback(rows)
        used_fallback = header_idx >= 0
    if header_idx < 0:
        raise ValueError(
            "헤더 행을 찾을 수 없습니다. 첫 행에 '이름'(또는 성명/학생명), '연락처'(또는 학부모전화/전화번호) 등 컬럼명이 있어야 합니다."
        )
    if used_fallback:
        logger.info(
            "excel_parsing: 표준 헤더(이름+전화 동시) 없음 → 첫 행 기준으로 컬럼 매칭 사용 (row=%s)",
            header_idx,
        )

    header_row = rows[header_idx]
    col = _build_header_map(header_row)
    name_col = col.get("name")
    parent_col = col.get("parent_phone")
    student_col = col.get("student_phone")
    school_col = col.get("school")
    grade_col = col.get("grade")
    remark_col = col.get("remark")

    result: list[dict[str, Any]] = []
    for r in range(header_idx + 1, len(rows)):
        row = rows[r]
        if remark_col is not None and "예시" in _cell_str(row, remark_col):
            continue

        name = _cell_str(row, name_col)
        student_phone_raw = _to_raw_phone(_cell_str(row, student_col))
        parent_phone_raw = _to_raw_phone(_cell_str(row, parent_col))

        if len(student_phone_raw) == 8 and student_phone_raw.isdigit():
            student_phone = "010" + student_phone_raw
            uses_identifier = True
        elif student_phone_raw and len(student_phone_raw) == 11 and student_phone_raw.startswith("010"):
            student_phone = student_phone_raw
            uses_identifier = False
        else:
            if not parent_phone_raw or len(parent_phone_raw) != 11 or not parent_phone_raw.startswith("010"):
                if not name:
                    continue
            student_phone = ""
            uses_identifier = True

        if not parent_phone_raw or len(parent_phone_raw) != 11 or not parent_phone_raw.startswith("010"):
            parent_phone_raw = student_phone if student_phone else ""

        if not name and not student_phone_raw and not parent_phone_raw:
            continue

        school_cell = _cell_str(row, school_col)
        grade_cell = _cell_str(row, grade_col)
        school_parsed, grade_parsed = _parse_school_grade(school_cell)
        school = school_parsed or school_cell
        grade = grade_parsed or grade_cell
        school_type = _infer_school_type(school)

        result.append({
            "name": name,
            "parent_phone": parent_phone_raw or student_phone,
            "phone": student_phone if student_phone else None,
            "studentPhone": student_phone if student_phone else None,
            "school": school,
            "grade": grade,
            "school_type": school_type,
            "schoolClass": _cell_str(row, col.get("school_class")),
            "major": _cell_str(row, col.get("major")),
            "memo": _cell_str(row, col.get("memo")),
            "gender": _cell_str(row, col.get("gender")).upper()[:1] or None,
            "uses_identifier": uses_identifier,
            "high_school_class": _cell_str(row, col.get("school_class")),
        })

    return result


class ExcelParsingService:
    """
    엑셀 파싱 + 등록 원테이크 (워커 전용).
    - lecture_id 있음: R2 다운로드 → 파싱 → 강의 수강 등록.
    - lecture_id 없음: R2 다운로드 → 파싱 → 학생만 일괄 생성.
    """

    def __init__(self, storage: IObjectStorage) -> None:
        self._storage = storage

    def run(
        self,
        job_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        payload:
          - file_key: str (R2 객체 키)
          - bucket: str (선택)
          - tenant_id: int (필수)
          - initial_password: str (필수, 4자 이상)
          - lecture_id: int (선택) — 있으면 수강등록, 없으면 학생만 일괄 생성
          - session_id: int (선택, lecture_id 있을 때만)
        """
        import tempfile

        file_key = payload.get("file_key")
        if not file_key:
            raise ValueError("payload.file_key required")

        bucket = (
            payload.get("bucket")
            or os.environ.get("EXCEL_BUCKET_NAME")
            or "academy-excel"
        )
        tenant_id = payload.get("tenant_id")
        lecture_id = payload.get("lecture_id")
        session_id = payload.get("session_id")
        initial_password = (payload.get("initial_password") or "").strip()

        if not tenant_id:
            raise ValueError("payload.tenant_id required")
        if len(initial_password) < 4:
            raise ValueError("payload.initial_password 4자 이상 필요")

        tmp_dir = Path(tempfile.gettempdir())
        local_path = tmp_dir / f"excel_job_{job_id}.xlsx"

        try:
            self._storage.download_to_path(bucket, file_key, str(local_path))
            rows = parse_student_excel_file(str(local_path))
            if not rows:
                raise ValueError("등록할 학생 데이터가 없습니다.")

            if lecture_id is not None:
                from apps.domains.enrollment.services import lecture_enroll_from_excel_rows

                result = lecture_enroll_from_excel_rows(
                    tenant_id=int(tenant_id),
                    lecture_id=int(lecture_id),
                    students_data=rows,
                    initial_password=initial_password,
                    session_id=int(session_id) if session_id is not None else None,
                )
                return result

            from apps.domains.students.services import bulk_create_students_from_excel_rows

            result = bulk_create_students_from_excel_rows(
                tenant_id=int(tenant_id),
                students_data=rows,
                initial_password=initial_password,
            )
            return result
        finally:
            # 더블 체크: 다운로드 성공/실패/부분 생성 여부와 관계없이 로컬 파일 삭제 시도
            cleaned = False
            if local_path.exists():
                try:
                    local_path.unlink()
                    cleaned = True
                except OSError as e:
                    logger.warning("Failed to remove tmp file %s: %s", local_path, e)
            if not cleaned and local_path.exists():
                logger.error("Local tmp file still exists after unlink attempt: %s", local_path)
