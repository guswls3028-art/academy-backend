# PATH: apps/domains/results/views/__init__.py
# apps/domains/results/views/__init__.py

"""
results.views public exports

==========================================================================================
Result vs ExamResult compatibility rule
==========================================================================================

이 프로젝트는 역사적으로 두 개의 결과 모델이 공존할 수 있다.

1) apps.domains.results.models.Result / ResultItem / ResultFact / ExamAttempt
   - 목적: "시험 운영/재시험/대표 attempt/append-only" 기반의 SSOT
   - Admin/Teacher 통계, 재채점, 대표 attempt 교체, 오답노트(append-only Fact 기반) 등에 사용
   - 이 계열 API는 target_type="exam" + target_id(exam_id) + enrollment_id가 핵심 키

2) apps.domains.results.models.exam_result.ExamResult
   - 목적: 레거시 public API 호환 (/api/v1/results/*) 및 과거 기능 유지
   - 안전한 데이터 마이그레이션 계획 없이 삭제/스키마 변경 금지
   - 이 계열 API는 기존 프론트/운영이 의존할 수 있으므로 계약을 보존한다.

원칙:
- 신규 기능/정합성 핵심은 1) Result/Fact/Attempt 기반으로 구현한다.
- 2) ExamResult는 legacy compatibility snapshot으로만 유지한다.
- 죽은 serializer/service/view 경로는 되살리지 않는다.
==========================================================================================
"""

# ======================================================
# Student-facing
# ======================================================
from .student_exam_result_view import MyExamResultView
from .wrong_note_view import WrongNoteView

# ======================================================
# Admin / Teacher-facing (대표 View만)
# ======================================================
from .admin_exam_results_view import AdminExamResultsView
from .admin_exam_summary_view import AdminExamSummaryView

__all__ = [
    "MyExamResultView",
    "WrongNoteView",
    "AdminExamResultsView",
    "AdminExamSummaryView",
]
