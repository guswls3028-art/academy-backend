# apps/domains/student_app/exams/views.py
"""
학생 앱 시험 API — 실제 DB 기반 (ExamEnrollment 기준)
GET /student/exams/            → 내가 응시 가능한 시험 목록
GET /student/exams/<id>/        → 시험 상세 (접근 권한 검사)
GET /student/exams/<id>/questions/ → 문항 목록 (번호/배점, 답 입력용)
"""
from __future__ import annotations

import logging
logger = logging.getLogger(__name__)

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from apps.support.student_app.exam_dependencies import (
    StudentExamSubmitError,
    create_online_exam_submission,
    dispatch_student_exam_submission,
    get_enrollment_for_student_exam,
    student_exam_queryset,
    student_exam_questions,
    submission_status_map_for_student_exams,
)
from .serializers import StudentExamSerializer


def _exam_queryset_for_student(student, tenant, *, include_upcoming_days: int = 0):
    """Enrollment 기준: request student + tenant 로 연결.

    2026-05-13 학원장 결정 정합: Exam.status 단위 폐기 → 학생별 Achievement SSOT.
    이전엔 `.exclude(status=CLOSED)` 로 학생 화면에서 CLOSED 시험을 숨겼으나,
    학원장 결정(시험 만들면 영구 운영)과 충돌하므로 제거.
    학원장이 의도적으로 응시 윈도를 좁히려면 open_at/close_at 시간 필드를 명시.
    """
    return student_exam_queryset(
        student,
        tenant,
        include_upcoming_days=include_upcoming_days,
    )


def _serialize_exam(exam, *, submission_status_map=None):
    """Exam → StudentExamSerializer 호환 dict."""
    session_id = None
    if hasattr(exam, "sessions") and exam.sessions.exists():
        first = exam.sessions.first()
        if first:
            session_id = first.id

    sub_info = (submission_status_map or {}).get(exam.id, {})

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
        "has_result": sub_info.get("has_result", False),
        "attempt_count": sub_info.get("attempt_count", 0),
    }).data


class StudentExamListView(APIView):
    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"items": []})
        request_student = get_request_student(request)
        if not request_student:
            return Response({"items": []})
        include_upcoming = str(request.query_params.get("include_upcoming") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        qs = _exam_queryset_for_student(
            request_student,
            tenant,
            include_upcoming_days=7 if include_upcoming else 0,
        )
        exams = list(qs)

        # 제출 상태 일괄 조회 (N+1 방지)
        submission_status_map = submission_status_map_for_student_exams(
            tenant=tenant,
            student=request_student,
            exams=exams,
        )

        items = [_serialize_exam(exam, submission_status_map=submission_status_map) for exam in exams]
        return Response({"items": items})


class StudentExamDetailView(APIView):
    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, pk):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        request_student = get_request_student(request)
        if not request_student:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        qs = _exam_queryset_for_student(request_student, tenant).filter(id=pk)
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
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        request_student = get_request_student(request)
        if not request_student:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        qs = _exam_queryset_for_student(request_student, tenant).filter(id=pk)
        exam = qs.first()
        if not exam:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            questions = student_exam_questions(exam)
        except Exception:
            return Response(
                {"detail": "시험 문항 정보를 불러올 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(questions)


def _get_enrollment_for_exam(student, exam_id, tenant=None):
    """시험 응시 권한이 있는 enrollment 한 개 반환. (enrollment, tenant) 또는 (None, None)."""
    return get_enrollment_for_student_exam(student, exam_id, tenant=tenant)


class StudentExamSubmitView(APIView):
    """POST /student/exams/<pk>/submit/ — ONLINE 답안 제출 (payload.answers: [{ exam_question_id, answer }])"""
    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def post(self, request, pk):
        exam_id = int(pk)
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        request_student = get_request_student(request)
        if not request_student:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        qs = _exam_queryset_for_student(request_student, tenant).filter(id=exam_id)
        exam = qs.first()
        if not exam:
            return Response(
                {"detail": "시험을 찾을 수 없거나 응시 권한이 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        enrollment, tenant = _get_enrollment_for_exam(request_student, exam_id, tenant=tenant)
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
        try:
            submission = create_online_exam_submission(
                request_user=request.user,
                request_student=request_student,
                tenant=tenant,
                exam=exam,
                enrollment=enrollment,
                answers=answers,
            )
        except StudentExamSubmitError as exc:
            return Response({"detail": exc.detail}, status=exc.status_code)

        try:
            dispatch_student_exam_submission(submission)
        except Exception:
            logger.exception("StudentExamSubmitView dispatch_submission failed")
            return Response(
                {"detail": "제출 처리 중 오류가 발생했습니다."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {"submission_id": submission.id, "status": getattr(submission, "status", submission.status)},
            status=status.HTTP_201_CREATED,
        )
