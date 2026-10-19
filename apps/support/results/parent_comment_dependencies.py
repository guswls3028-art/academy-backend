"""Cross-domain dependencies for explicit parent assessment feedback."""
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import ExamEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Session
from apps.domains.student_app.permissions import get_request_student, is_request_parent

__all__ = ["Enrollment", "SessionEnrollment", "ExamEnrollment", "HomeworkAssignment", "Homework", "Session", "get_request_student", "is_request_parent"]

from apps.domains.enrollment.selectors import learning_history_enrollments_for_student
from apps.support.results.progress_read_dependencies import session_score_enrollment_ids
from apps.support.progress.session_calculator_dependencies import get_exam_target_enrollment_pairs_for_session
