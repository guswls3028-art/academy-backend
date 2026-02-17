# PATH: src/application/services/excel_parsing_service.py
# 비즈니스 로직의 핵심: 엑셀 파싱 + 강의 수강 등록 원테이크 (헥사고날 Application Service)
# - 엑셀 파싱·등록 흐름은 이 서비스만 통과 (워커/API 공통)
# - "무적의 엑셀 파싱": 헤더 별칭 매칭, 이름·학부모전화 기준 행 파싱

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

from src.application.ports.storage import IObjectStorage

logger = logging.getLogger(__name__)

# 헤더 별칭 (학원별 양식 대응 — 양식 안 맞춰도 인식되도록 넓게)
# 완전 일치 + 부분 포함(contains) 둘 다 시도
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "name": (
        "이름", "성명", "학생명", "학생 이름", "이름(학생)", "성함", "학생성명",
    ),
    "parent_phone": (
        "학부모전화번호", "부모핸드폰", "부모 전화", "학부모 전화", "보호자 전화", "보호자전화",
        "학부모연락처", "부모 연락처", "보호자 연락처", "연락처(학부모)", "전화(학부모)",
        "휴대폰", "핸드폰", "연락처", "전화번호", "전화", "폰", "폰번호",
        "부모핸드", "학부모", "보호자",
    ),
    "student_phone": (
        "학생전화번호", "학생핸드폰", "학생 전화", "학생연락처", "학생 연락처",
        "연락처(학생)", "전화(학생)", "학생폰", "학생 폰",
        "학생핸드", "학생전화",
    ),
    "school": ("학교", "학교(학년)", "학교명", "출신학교", "학교(학년)"),
    "grade": ("학년", "학년도"),
    "gender": ("성별", "남자", "여자", "남성", "여성", "남", "여", "녀"),
    "school_class": ("반", "학급"),
    "major": ("계열", "이과", "문과"),
    "memo": ("메모", "비고", "특이사항", "비고2"),
    "remark": ("구분", "체크", "비고", "비고2", "출석", "현장"),
}


def _normalize_header(label: str) -> str:
    """공백 제거, 전각→반각, 소문자."""
    s = (label or "").strip()
    s = re.sub(r"\s", "", s)
    s = "".join(chr(ord(c) - 0xFEE0) if "\uFF01" <= c <= "\uFF5E" else c for c in s)
    return s.lower()


def _match_header(cell: str, key: str) -> bool:
    """완전 일치 또는 짧은 별칭이 헤더 시작/포함 시 매칭 (전각 숫자 정규화)."""
    norm = _normalize_header(cell)
    if not norm:
        return False
    for alias in HEADER_ALIASES.get(key, ()):
        a = _normalize_header(alias)
        if not a:
            continue
        if norm == a:
            return True
        if len(a) >= 3 and a in norm:
            return True
        if len(a) >= 2 and norm.startswith(a) and len(norm) <= len(a) + 4:
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
    """표준 헤더를 못 찾았을 때: 상위 10행 스캔, 이름/전화 컬럼 하나라도 있으면 헤더로 사용."""
    for i, row in enumerate(rows[:10]):
        if not row:
            continue
        col = _build_header_map(row)
        if col.get("name") is not None or col.get("parent_phone") is not None or col.get("student_phone") is not None:
            return i
    return -1


def _infer_missing_columns(
    col: dict[str, int], header_row: list[Any], rows: list[list[Any]], header_idx: int
) -> dict[str, int]:
    """
    필수 컬럼(name, parent_phone)이 없을 때, 샘플 데이터로 컬럼 추측.
    - 010 11자리 패턴이 여러 행에 나오는 컬럼 → parent_phone 후보
    - 2~4글자 한글(이름형)이 여러 행에 나오는 컬럼 → name 후보
    """
    out = dict(col)
    sample = rows[header_idx + 1 : header_idx + 21]  # 최대 20행 샘플
    if not sample:
        return out

    phone_col_candidates: list[tuple[int, int]] = []
    name_col_candidates: list[tuple[int, int]] = []
    korean_name = re.compile(r"^[가-힣]{2,4}[A-Za-z0-9]*\*?$")

    for ci in range(max(len(r) for r in sample) if sample else 0):
        phone_hits = 0
        name_hits = 0
        for row in sample:
            if ci >= len(row):
                continue
            v = str(row[ci] or "").strip()
            if re.match(r"^010[0-9]{8}$", _to_raw_phone(v)):
                phone_hits += 1
            if korean_name.match(v):
                name_hits += 1
        if phone_hits >= 2:
            phone_col_candidates.append((ci, phone_hits))
        if name_hits >= 2:
            name_col_candidates.append((ci, name_hits))

    if out.get("parent_phone") is None and phone_col_candidates:
        phone_col_candidates.sort(key=lambda x: -x[1])
        out["parent_phone"] = phone_col_candidates[0][0]
    if out.get("name") is None and name_col_candidates:
        name_col_candidates.sort(key=lambda x: -x[1])
        out["name"] = name_col_candidates[0][0]

    return out


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


def _row_looks_like_student(
    name: str,
    parent_phone_raw: str,
    student_phone_raw: str,
) -> bool:
    """
    학생 행 여부 스코어링. 소제목/날짜/빈 행 등 비학생 행 제외.
    - 이름이 날짜패턴(01월, 2/7~)이면 제외
    - 이름이 순수 숫자/너무 길면 제외
    - 최소 name 또는 유효 전화번호 1개 이상 필요
    """
    name = (name or "").strip()
    if not name and not parent_phone_raw and not student_phone_raw:
        return False
    if re.match(r"^\d{1,2}/\d{1,2}", name) or re.match(r"^\d{1,2}월\s*$", name):
        return False
    if name and re.match(r"^\d+$", name):
        return False
    if len(name) > 20:
        return False
    has_phone = (
        (len(parent_phone_raw) == 11 and parent_phone_raw.startswith("010"))
        or (len(student_phone_raw) == 11 and student_phone_raw.startswith("010"))
        or (len(parent_phone_raw) == 8 and parent_phone_raw.isdigit())
        or (len(student_phone_raw) == 8 and student_phone_raw.isdigit())
    )
    has_name = bool(re.match(r"^[가-힣]{2,5}[A-Za-z0-9]*\*?$", name))
    if not has_name and not has_phone:
        return False
    return True


def _extract_lecture_title(rows: list[list[Any]], header_idx: int) -> str:
    """헤더 행 위(0 ~ header_idx-1)에서 강의 제목처럼 보이는 셀 추출."""
    candidates: list[str] = []
    for r in range(min(header_idx, 5)):  # 최대 5행까지
        for cell in rows[r] if r < len(rows) else []:
            s = str(cell or "").strip()
            if len(s) >= 4 and len(s) <= 120 and not s.isdigit():
                candidates.append(s)
    return candidates[0] if candidates else ""


def parse_student_excel_file(local_path: str) -> tuple[list[dict[str, Any]], str]:
    """
    로컬 엑셀 파일을 파싱하여 강의 수강 등록용 행 리스트와 강의 제목 반환.
    양식 안 맞춰도 인식: 헤더 별칭 넓게 지원, 이름+전화 없으면 첫 행 기준으로도 시도.
    Returns: (rows, lecture_title)
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
        return [], ""

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
    if col.get("name") is None or col.get("parent_phone") is None:
        col = _infer_missing_columns(col, header_row, rows, header_idx)
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

        if not _row_looks_like_student(name, parent_phone_raw, student_phone_raw):
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

    lecture_title = _extract_lecture_title(rows, header_idx)
    return result, lecture_title


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
        *,
        on_progress: Callable[[str, int], None] | None = None,
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
            rows, lecture_title = parse_student_excel_file(str(local_path))
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
                if isinstance(result, dict) and lecture_title:
                    result["lecture_title"] = lecture_title
                return result

            from apps.domains.students.services import bulk_create_students_from_excel_rows

            _last_pct: list[int] = [-1]  # mutable for closure

            def _row_progress(current: int, total: int) -> None:
                if on_progress and total > 0:
                    pct = min(95, 40 + int(55 * current / total))
                    if pct - _last_pct[0] >= 5 or current == total:
                        _last_pct[0] = pct
                        on_progress("creating", pct)

            result = bulk_create_students_from_excel_rows(
                tenant_id=int(tenant_id),
                students_data=rows,
                initial_password=initial_password,
                on_row_progress=_row_progress if on_progress else None,
            )
            if isinstance(result, dict) and lecture_title:
                result["lecture_title"] = lecture_title
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
