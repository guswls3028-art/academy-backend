# apps/domains/results/views/__init__.py

"""
results.views public exports

⚠️ IMPORTANT RULES
- 이 파일은 "외부에서 import 해도 되는 View"만 export한다.
- urls.py에서 직접 import하는 View는 굳이 여기서 export하지 않아도 된다.
- 존재하지 않는 파일 / 클래스가 import되면
  migrate / runserver 시점에 즉시 크래시 난다.
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

# ======================================================
# ⚠️ Question stats 관련
# - AdminExamQuestionStatsView 등은
#   urls.py에서 직접 import하므로 여기서는 export하지 않는다.
# - 중복 export / 불필요한 import를 방지하기 위함
# ======================================================

__all__ = [
    "MyExamResultView",
    "WrongNoteView",
    "AdminExamResultsView",
    "AdminExamSummaryView",
]
