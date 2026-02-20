# apps/domains/student_app/exams/views.py
"""
학생 앱 시험 API — 실제 DB 기반 (ExamEnrollment 기준)
GET /student/exams/            → 내가 응시 가능한 시험 목록
GET /student/exams/<id>/        → 시험 상세 (접근 권한 검사)
GET /student/exams/<id>/questions/ → 문항 목록 (번호/배점, 답 입력용)
"""
from __future__ import annotations

from django.utils import timezone
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.domains.student_app.permissions import IsStudentOrParent
from apps.domains.exams.models import Exam, ExamQuestion
from apps.domains.exams.services.template_resolver import resolve_template_exam
from .serializers import StudentExamSerializer


def _exam_queryset_for_user(user):
    """Enrollment 기준: student.user 로 연결."""
    now = timezone.now()
    return (
        Exam.objects.filter(
            exam_type=Exam.ExamType.REGULAR,
            exam_enrollments__enrollment__student__user=user,
            is_active=True,
        )
        .filter(
            Q(open_at__isnull=True) | Q(open_at__lte=now),
            Q(close_at__isnull=True) | Q(close_at__gte=now),
        )
        .distinct()
        .order_by("open_at", "id")
    )


def _serialize_exam(exam):
    """Exam → StudentExamSerializer 호환 dict."""
    session_id = None
    if hasattr(exam, "sessions") and exam.sessions.exists():
        first = exam.sessions.first()
        if first:
            session_id = first.id
    return StudentExamSerializer({
        "id": exam.id,
        "title": exam.title,
        "description": getattr(exam, "description", "") or "",
        "open_at": exam.open_at,
        "close_at": exam.close_at,
        "allow_retake": bool(getattr(exam, "allow_retake", False)),
        "max_attempts": int(getattr(exam, "max_attempts", 1) or 1),
        "pass_score": int(getattr(exam, "pass_score", 0) or 0),
        "session_id": session_id,
    }).data


class StudentExamListView(APIView):
    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        qs = _exam_queryset_for_user(request.user)
        items = [_serialize_exam(exam) for exam in qs]
        return Response({"items": items})


class StudentExamDetailView(APIView):
    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, pk):
        qs = _exam_queryset_for_user(request.user).filter(id=pk)
        exam = qs.first()
        if not exam:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_serialize_exam(exam))


class StudentExamQuestionsView(APIView):
    """학생용 시험 문항 목록 (번호/배점). 접근 권한 검사 후 템플릿 기준 문항 반환."""
    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, pk):
        qs = _exam_queryset_for_user(request.user).filter(id=pk)
        exam = qs.first()
        if not exam:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            template = resolve_template_exam(exam)
        except Exception:
            return Response(
                {"detail": "시험 문항 정보를 불러올 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        questions = (
            ExamQuestion.objects.filter(sheet__exam=template)
            .order_by("number")
            .values("id", "number", "score")
        )
        return Response(list(questions))


def _get_enrollment_for_exam(user, exam_id):
    """시험 응시 권한이 있는 enrollment 한 개 반환. (enrollment, tenant) 또는 (None, None)."""
    from apps.domains.exams.models import ExamEnrollment
    from apps.domains.enrollment.models import Enrollment
    from apps.domains.students.models import Student

    student = getattr(user, "student_profile", None)
    if not student:
        try:
            student = Student.objects.filter(user=user).first()
        except Exception:
            return None, None
    if not student:
        return None, None
    ee = (
        ExamEnrollment.objects.filter(
            exam_id=int(exam_id),
            enrollment__student=student,
        )
        .select_related("enrollment", "enrollment__tenant")
        .first()
    )
    if not ee or not ee.enrollment:
        return None, None
    return ee.enrollment, getattr(ee.enrollment, "tenant", None)


class StudentExamSubmitView(APIView):
    """POST /student/exams/<pk>/submit/ — ONLINE 답안 제출 (payload.answers: [{ exam_question_id, answer }])"""
    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def post(self, request, pk):
        exam_id = int(pk)
        qs = _exam_queryset_for_user(request.user).filter(id=exam_id)
        exam = qs.first()
        if not exam:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        enrollment, tenant = _get_enrollment_for_exam(request.user, exam_id)
        if not enrollment or not tenant:
            return Response(
                {"detail": "응시 대상이 아닙니다."},
                status=status.HTTP_403_FORBIDDEN,
            )
        answers = request.data.get("answers")
        if not isinstance(answers, (list, dict)):
            return Response(
                {"detail": "answers 필드가 필요합니다 (리스트 또는 객체)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if isinstance(answers, dict):
            answers = [{"exam_question_id": int(k), "answer": str(v)} for k, v in answers.items()]
        else:
            answers = [
                {"exam_question_id": int(a.get("exam_question_id")), "answer": str(a.get("answer", ""))}
                for a in answers if a.get("exam_question_id") is not None
            ]
        if not answers:
            return Response(
                {"detail": "최소 1개 문항의 답을 입력하세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from apps.domains.submissions.models import Submission
        from apps.domains.submissions.services.dispatcher import dispatch_submission

        submission = Submission.objects.create(
            tenant=tenant,
            user=request.user,
            enrollment_id=enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam_id,
            source=Submission.Source.ONLINE,
            payload={"answers": answers},
            status=Submission.Status.SUBMITTED,
        )
        try:
            dispatch_submission(submission)
        except Exception as e:
            return Response(
                {"detail": getattr(e, "message", str(e)) or "제출 처리 중 오류가 발생했습니다."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {"submission_id": submission.id, "status": getattr(submission, "status", submission.status)},
            status=status.HTTP_201_CREATED,
        )
