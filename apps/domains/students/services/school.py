# PATH: apps/domains/students/services/school.py
"""
학교명(XX중, XX고)으로 school_type 및 high_school/middle_school 자동 정리.
엑셀 업로드 시 학교 타입 미기입이어도 학교명만 있으면 반영.
"""
from typing import Tuple

# HIGH / MIDDLE
SchoolType = str


def normalize_school_from_name(
    school_name: str | None,
    school_type_from_input: str | None = None,
) -> Tuple[SchoolType, str | None, str | None]:
    """
    학교명과 (선택) 입력된 학교 타입으로 (school_type, high_school, middle_school) 반환.

    - 학교명 비어있음 → high_school/middle_school 은 None, school_type 은 입력값 또는 "HIGH".
    - 학교명에 "고" 포함(예: 숙명여고, 중동고) → HIGH, high_school=학교명, middle_school=None.
    - 학교명에 "중" 포함(예: 휘문중, XX중) → MIDDLE, middle_school=학교명, high_school=None.
    - 둘 다 포함(예: 중동고) → "고" 우선 적용하여 HIGH.
    - 그 외 → school_type_from_input 또는 "HIGH" 로 high_school/middle_school 배치.
    """
    name = (school_name or "").strip() or None
    default_type = (school_type_from_input or "HIGH").upper()
    if default_type not in ("HIGH", "MIDDLE"):
        default_type = "HIGH"

    if not name:
        return (default_type, None, None)

    # "고"가 있으면 고등(중동고, XX고등학교 등)
    if "고" in name:
        return ("HIGH", name, None)
    if "중" in name:
        return ("MIDDLE", None, name)

    # 구분 불가 시 입력 타입으로
    if default_type == "HIGH":
        return ("HIGH", name, None)
    return ("MIDDLE", None, name)
