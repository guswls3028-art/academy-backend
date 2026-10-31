"""Explicit cross-domain dependencies for student QR attendance."""

from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.services.lifecycle import change_participant_status, checkout_participant
from apps.domains.clinic.time_ranges import booking_window, session_window
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.lectures.models import Lecture
from apps.domains.student_app.permissions import IsStudent, get_request_student
from apps.domains.students.models import Student

__all__ = [
    "SessionParticipant", "change_participant_status", "checkout_participant",
    "booking_window", "session_window", "Enrollment", "SessionEnrollment",
    "Lecture", "IsStudent", "get_request_student", "Student",
]
