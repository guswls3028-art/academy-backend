# PATH: apps/domains/clinic/views/__init__.py
"""
Clinic views package.
Re-exports all public symbols for backward compatibility.
  - from apps.domains.clinic.views import SessionViewSet  (still works)
"""

from .session_views import SessionViewSet
from .participant_views import ParticipantViewSet
from .test_views import TestViewSet
from .submission_views import SubmissionViewSet
from .settings_views import ClinicSettingsView
from .idcard_views import StudentClinicIdcardView

__all__ = [
    "SessionViewSet",
    "ParticipantViewSet",
    "TestViewSet",
    "SubmissionViewSet",
    "ClinicSettingsView",
    "StudentClinicIdcardView",
]
