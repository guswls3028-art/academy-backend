# apps/domains/assets/omr/services/meta_generator.py
"""
Objective OMR template meta (SSOT).

- GET /api/v1/assets/omr/objective/meta/?question_count=10|20|30 에서 사용.
- 좌표는 mm 단위 (A4 landscape 297x210 기준).
"""

ALLOWED_QUESTION_COUNTS = (10, 20, 30)


def build_objective_template_meta(*, question_count: int, **kwargs):
    """
    question_count(10|20|30)에 맞는 템플릿 meta 반환.
    OmrObjectiveMetaV1 형식: version, units, question_count, questions[].roi (mm).
    """
    if question_count not in ALLOWED_QUESTION_COUNTS:
        raise ValueError("question_count must be one of 10, 20, 30")

    # A4 landscape 297x210mm, 우측 정렬 버블 영역 가정 (대략 x=200, 세로 간격 12mm)
    questions = []
    for i in range(1, question_count + 1):
        y_mm = 30 + (i - 1) * 12
        questions.append({
            "question_number": i,
            "axis": "y",
            "roi": {"x": 200, "y": y_mm, "w": 8, "h": 8},
            "choices": [{"value": str(j)} for j in range(1, 6)],
        })

    return {
        "version": "objective_v1",
        "units": "mm",
        "question_count": question_count,
        "questions": questions,
    }
