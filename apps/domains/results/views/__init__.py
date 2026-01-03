# apps/domains/results/views/__init__.py
from .exam_result_view import ExamStatsView, ExamQuestionStatsView
from .wrong_note_view import WrongNoteView

__all__ = [
    "ExamStatsView",
    "ExamQuestionStatsView",
    "WrongNoteView",
]
