# apps/domains/results/views/__init__.py

from .admin_exam_results_view import AdminExamResultsView
from .admin_exam_summary_view import AdminExamSummaryView
from .admin_exam_question_stats_view import AdminExamQuestionStatsView
from .student_exam_result_view import MyExamResultView
from .wrong_note_view import WrongNoteView

__all__ = [
    "AdminExamResultsView",
    "AdminExamSummaryView",
    "AdminExamQuestionStatsView",
    "MyExamResultView",
    "WrongNoteView",
]
