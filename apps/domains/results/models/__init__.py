# apps/domains/results/models/__init__.py

from .result import Result
from .result_item import ResultItem
from .result_fact import ResultFact
from .exam_attempt import ExamAttempt
from .submission_answer import SubmissionAnswer
from .wrong_note_pdf import WrongNotePDF

__all__ = [
    "Result",
    "ResultItem",
    "ResultFact",
    "ExamAttempt",
    "SubmissionAnswer",
    "WrongNotePDF",
]
