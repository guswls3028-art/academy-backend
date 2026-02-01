# apps/domains/results/models/__init__.py

from .result import Result
from .result_item import ResultItem
from .result_fact import ResultFact
from .exam_attempt import ExamAttempt
from .wrong_note_pdf import WrongNotePDF
from .exam_result import ExamResult

# ❌ SubmissionAnswer 제거됨 (raw input은 submissions 도메인 책임)

__all__ = [
    "Result",
    "ResultItem",
    "ResultFact",
    "ExamAttempt",
    "WrongNotePDF",
    "ExamResult",
]
