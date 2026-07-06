"""Cross-domain dependencies for attendance views."""

from __future__ import annotations

from apps.domains.ai.gateway import dispatch_job
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import ExamEnrollment
from apps.domains.fees.services import deactivate_fees_for_enrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.lectures.models import Session
from apps.domains.messaging.services import send_event_notification
from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map
from apps.domains.results.utils.session_exam import get_exams_for_session
