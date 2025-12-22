from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from .models import Session, SessionParticipant, Test, Submission
from .serializers import (
    ClinicSessionSerializer,
    ClinicSessionParticipantSerializer,
    ClinicSessionParticipantCreateSerializer,
    ClinicTestSerializer,
    ClinicSubmissionSerializer,
)
from .filters import SessionFilter, SubmissionFilter

from apps.support.messaging.services import send_clinic_reminder_for_students
