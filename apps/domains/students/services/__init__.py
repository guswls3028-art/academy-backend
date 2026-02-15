# PATH: apps/domains/students/services/__init__.py
from .school import normalize_school_from_name
from .lecture_enroll import get_or_create_student_for_lecture_enroll

__all__ = ["normalize_school_from_name", "get_or_create_student_for_lecture_enroll"]
