# PATH: apps/domains/students/services/school.py
"""
학교명(XX초, XX중, XX고)으로 school_type 및 school 필드 자동 정리.
school_level_mode 기반 validation 헬퍼 포함.
"""
from typing import Tuple

# ELEMENTARY / MIDDLE / HIGH
SchoolType = str

# 학교급별 허용 학년 범위
GRADE_RANGE: dict[str, tuple[int, int]] = {
    "ELEMENTARY": (1, 6),
    "MIDDLE": (1, 3),
    "HIGH": (1, 3),
}

ALL_SCHOOL_TYPES = {"ELEMENTARY", "MIDDLE", "HIGH"}


def get_valid_school_types(school_level_mode: str | None = None) -> set[str]:
    """school_level_mode에 따른 허용 학교급 반환."""
    mode = (school_level_mode or "middle_high").lower()
    if mode == "elementary_middle":
        return {"ELEMENTARY", "MIDDLE"}
    return {"MIDDLE", "HIGH"}


def get_valid_grades(school_type: str) -> set[int]:
    """school_type에 따른 허용 학년 set 반환."""
    lo, hi = GRADE_RANGE.get(school_type, (1, 3))
    return set(range(lo, hi + 1))


def get_max_grade(school_type: str) -> int:
    """school_type의 최대 학년 반환."""
    _, hi = GRADE_RANGE.get(school_type, (1, 3))
    return hi


def is_valid_grade(school_type: str, grade: int | None) -> bool:
    """학년이 해당 school_type에 유효한지 확인."""
    if grade is None:
        return True
    lo, hi = GRADE_RANGE.get(school_type, (1, 3))
    return lo <= grade <= hi


def normalize_school_from_name(
    school_name: str | None,
    school_type_from_input: str | None = None,
) -> Tuple[SchoolType, str | None, str | None, str | None]:
    """
    학교명과 (선택) 입력된 학교 타입으로 반환:
    (school_type, elementary_school, high_school, middle_school)

    - 학교명 비어있음 → 모두 None, school_type은 입력값 또는 "HIGH".
    - "초등학교" 또는 "초"(끝) → ELEMENTARY
    - "고" 포함(숙명여고, 중동고) → HIGH
    - "중" 포함(휘문중, XX중) → MIDDLE
    - 둘 다 포함(중동고) → "고" 우선 HIGH.
    """
    name = (school_name or "").strip() or None
    default_type = (school_type_from_input or "HIGH").upper()
    if default_type not in ALL_SCHOOL_TYPES:
        default_type = "HIGH"

    if not name:
        return (default_type, None, None, None)

    # "초등학교" 또는 이름 끝이 "초"
    if "초등학교" in name or (name.endswith("초") and "고" not in name and "중" not in name):
        return ("ELEMENTARY", name, None, None)
    # "고"가 있으면 고등(중동고, XX고등학교 등)
    if "고" in name:
        return ("HIGH", None, name, None)
    if "중" in name:
        return ("MIDDLE", None, None, name)

    # 구분 불가 시 입력 타입으로
    if default_type == "ELEMENTARY":
        return ("ELEMENTARY", name, None, None)
    if default_type == "HIGH":
        return ("HIGH", None, name, None)
    return ("MIDDLE", None, None, name)
