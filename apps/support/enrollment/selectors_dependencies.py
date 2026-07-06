"""Cross-domain dependency loaders for enrollment selectors."""

from __future__ import annotations


def get_student_model():
    from apps.domains.students.models import Student

    return Student


def get_lecture_model():
    from apps.domains.lectures.models import Lecture

    return Lecture


def get_session_model():
    from apps.domains.lectures.models import Session

    return Session


def get_exam_enrollment_models():
    from apps.domains.exams.models import Exam, ExamEnrollment

    return Exam, ExamEnrollment


def get_homework_assignment_model():
    from apps.domains.homework.models import HomeworkAssignment

    return HomeworkAssignment


def get_homework_model():
    from apps.domains.homework_results.models import Homework

    return Homework
