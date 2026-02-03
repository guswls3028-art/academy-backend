# apps/domains/assets/omr/services/meta_generator.py
"""
TEMP STUB — SSOT ALIGN

- submissions 도메인이 기대하는 인터페이스만 유지한다.
- 실제 meta 생성 책임은 현재 외부 파이프라인/워커 영역이다.
- 이 함수는 존재 보장용이며, 호출되면 명시적으로 실패시킨다.
"""

def build_objective_template_meta(*, question_count: int, **kwargs):
    """
    Stub for legacy compatibility.

    Args:
        question_count (int): number of questions (expected 10|20|30)

    Raises:
        NotImplementedError: always
    """
    raise NotImplementedError(
        "build_objective_template_meta is not implemented. "
        "Objective OMR meta is generated outside assets domain."
    )
