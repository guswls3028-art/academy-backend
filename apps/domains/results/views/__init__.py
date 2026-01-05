# PATH: apps/domains/results/views/__init__.py

from .exam_result_view import ExamStatsView, ExamQuestionStatsView
from .wrong_note_view import WrongNoteView

from .admin_exam_results_view import AdminExamResultsView
from .admin_exam_summary_view import AdminExamSummaryView
from .admin_exam_question_stats_view import AdminExamQuestionStatsView

__all__ = [
    # Legacy
    "ExamStatsView",
    "ExamQuestionStatsView",

    # Admin
    "AdminExamResultsView",
    "AdminExamSummaryView",
    "AdminExamQuestionStatsView",

    # Shared
    "WrongNoteView",
]
