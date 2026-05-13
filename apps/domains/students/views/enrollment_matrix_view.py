"""학생 단위 enrollment matrix view — Phase #11/#12 (2026-05-12).

학원장이 학생 1명 시점으로 강의 세션 list + 각 세션의 시험/과제 enrolled 여부 한 화면.

API:
  GET /api/v1/students/{id}/enrollment-matrix/?lecture_id=N
    → {enrollment_id, lecture: {id, title}, sessions: [{id, title, exams[], homeworks[]}]}

  POST /api/v1/students/{id}/enrollment-matrix/toggle/
    body: {target_type: "exam"|"homework"|"session", target_id, action: "add"|"remove"}

tenant 격리 — request.tenant + student.tenant + lecture.tenant 3중.
"""
from __future__ import annotations

from django.db import transaction
from rest_framework import status, views
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndStaff


class StudentEnrollmentMatrixView(views.APIView):
    """GET /students/{id}/enrollment-matrix/?lecture_id=N"""

    permission_classes = [TenantResolvedAndStaff]

    def get(self, request, student_id: int):
        tenant = request.tenant
        try:
            lecture_id = int(request.query_params.get("lecture_id") or 0)
        except ValueError:
            return Response({"detail": "lecture_id 잘못됨"}, status=status.HTTP_400_BAD_REQUEST)
        if lecture_id <= 0:
            return Response({"detail": "lecture_id 필수"}, status=status.HTTP_400_BAD_REQUEST)

        from apps.domains.students.models import Student
        from apps.domains.enrollment.models import Enrollment, SessionEnrollment
        from apps.domains.lectures.models import Lecture, Session
        from apps.domains.exams.models import Exam, ExamEnrollment
        from apps.domains.homework.models import HomeworkAssignment

        try:
            student = Student.objects.get(id=student_id, tenant=tenant, deleted_at__isnull=True)
        except Student.DoesNotExist:
            return Response({"detail": "학생을 찾을 수 없습니다"}, status=status.HTTP_404_NOT_FOUND)
        try:
            lecture = Lecture.objects.get(id=lecture_id, tenant=tenant)
        except Lecture.DoesNotExist:
            return Response({"detail": "강의를 찾을 수 없습니다"}, status=status.HTTP_404_NOT_FOUND)

        enrollment = Enrollment.objects.filter(
            tenant=tenant, student=student, lecture=lecture, status="ACTIVE",
        ).first()
        if not enrollment:
            return Response({
                "enrollment_id": None,
                "lecture": {"id": lecture.id, "title": lecture.title},
                "sessions": [],
                "detail": "이 학생은 해당 강의에 등록되어 있지 않습니다.",
            })

        sessions = list(
            Session.objects.filter(lecture=lecture).order_by("order", "id")
            .values("id", "title", "order")
        )
        session_ids = [s["id"] for s in sessions]

        # 학생의 SessionEnrollment 매핑
        enrolled_session_ids = set(
            SessionEnrollment.objects.filter(
                tenant=tenant, enrollment=enrollment, session_id__in=session_ids,
            ).values_list("session_id", flat=True)
        )

        # session 별 시험 메타 — Exam.sessions M2M 통해 batch fetch.
        exams_by_session: dict[int, list] = {}
        for exam in Exam.objects.filter(sessions__id__in=session_ids).distinct().prefetch_related("sessions"):
            for sid in exam.sessions.filter(id__in=session_ids).values_list("id", flat=True):
                exams_by_session.setdefault(sid, []).append({"id": exam.id, "title": exam.title})

        enrolled_exam_ids = set(
            ExamEnrollment.objects.filter(
                exam__sessions__id__in=session_ids,
                enrollment=enrollment,
            ).values_list("exam_id", flat=True)
        )

        # Homework — SSOT: HomeworkAssignment (homework-level enrollment).
        # session_scores_view 와 동일 SSOT. HomeworkEnrollment(session 단위) 사용 시
        # 같은 session N개 homework 가 enrolled 상태를 공유해 부정확.
        from apps.domains.homework_results.models import Homework
        homeworks_by_session: dict[int, list] = {}
        for hw in Homework.objects.filter(session_id__in=session_ids).values(
            "id", "title", "session_id",
        ):
            homeworks_by_session.setdefault(hw["session_id"], []).append(hw)

        enrolled_hw_ids: set[int] = set(
            HomeworkAssignment.objects.filter(
                tenant=tenant,
                enrollment=enrollment,
                session_id__in=session_ids,
            ).values_list("homework_id", flat=True)
        )

        result_sessions = []
        for s in sessions:
            sid = s["id"]
            result_sessions.append({
                "id": sid,
                "title": s["title"] or f"세션 {s['order']}",
                "order": s["order"],
                "session_enrolled": sid in enrolled_session_ids,
                "exams": [
                    {"id": e["id"], "title": e["title"], "enrolled": e["id"] in enrolled_exam_ids}
                    for e in exams_by_session.get(sid, [])
                ],
                "homeworks": [
                    {"id": h["id"], "title": h["title"], "enrolled": h["id"] in enrolled_hw_ids}
                    for h in homeworks_by_session.get(sid, [])
                ],
            })

        return Response({
            "enrollment_id": enrollment.id,
            "lecture": {"id": lecture.id, "title": lecture.title},
            "student": {"id": student.id, "name": getattr(student, "name", "") or ""},
            "sessions": result_sessions,
        })


class StudentEnrollmentMatrixToggleView(views.APIView):
    """POST /students/{id}/enrollment-matrix/toggle/
    body: {target_type: "exam"|"homework"|"session", target_id, action: "add"|"remove", lecture_id}
    """

    permission_classes = [TenantResolvedAndStaff]

    def post(self, request, student_id: int):
        tenant = request.tenant
        target_type = (request.data.get("target_type") or "").strip()
        action = (request.data.get("action") or "").strip()
        try:
            target_id = int(request.data.get("target_id") or 0)
            lecture_id = int(request.data.get("lecture_id") or 0)
        except (TypeError, ValueError):
            return Response({"detail": "target_id / lecture_id 잘못됨"}, status=status.HTTP_400_BAD_REQUEST)
        if target_type not in ("exam", "homework", "session"):
            return Response({"detail": "target_type 잘못됨"}, status=status.HTTP_400_BAD_REQUEST)
        if action not in ("add", "remove"):
            return Response({"detail": "action 잘못됨"}, status=status.HTTP_400_BAD_REQUEST)

        from apps.domains.students.models import Student
        from apps.domains.enrollment.models import Enrollment, SessionEnrollment
        from apps.domains.lectures.models import Lecture
        from apps.domains.exams.models import Exam, ExamEnrollment
        from apps.domains.homework_results.models import Homework
        from apps.domains.homework.models import HomeworkAssignment

        try:
            student = Student.objects.get(id=student_id, tenant=tenant)
        except Student.DoesNotExist:
            return Response({"detail": "학생을 찾을 수 없습니다"}, status=status.HTTP_404_NOT_FOUND)
        try:
            lecture = Lecture.objects.get(id=lecture_id, tenant=tenant)
        except Lecture.DoesNotExist:
            return Response({"detail": "강의를 찾을 수 없습니다"}, status=status.HTTP_404_NOT_FOUND)
        enrollment = Enrollment.objects.filter(
            tenant=tenant, student=student, lecture=lecture, status="ACTIVE",
        ).first()
        if not enrollment:
            return Response({"detail": "강의 등록 없음"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            if target_type == "session":
                if action == "add":
                    SessionEnrollment.objects.get_or_create(
                        tenant=tenant, enrollment=enrollment, session_id=target_id,
                    )
                else:
                    SessionEnrollment.objects.filter(
                        tenant=tenant, enrollment=enrollment, session_id=target_id,
                    ).delete()
            elif target_type == "exam":
                try:
                    exam = Exam.objects.get(id=target_id, tenant=tenant)
                except Exam.DoesNotExist:
                    return Response({"detail": "시험을 찾을 수 없습니다"}, status=status.HTTP_404_NOT_FOUND)
                if action == "add":
                    # ExamEnrollment 는 SessionEnrollment 부분집합 — exam.sessions M2M 의 첫 세션 자동 보강
                    first_session_id = exam.sessions.values_list("id", flat=True).first()
                    if first_session_id:
                        SessionEnrollment.objects.get_or_create(
                            tenant=tenant, enrollment=enrollment, session_id=first_session_id,
                        )
                    ExamEnrollment.objects.get_or_create(exam=exam, enrollment=enrollment)
                else:
                    ExamEnrollment.objects.filter(exam=exam, enrollment=enrollment).delete()
            else:  # homework
                # SSOT: HomeworkAssignment (homework-level). HomeworkEnrollment(session 단위)
                # 는 같은 session 의 다른 homework 까지 영향 → 부정확.
                try:
                    hw = Homework.objects.get(id=target_id, tenant=tenant)
                except Homework.DoesNotExist:
                    return Response({"detail": "과제를 찾을 수 없습니다"}, status=status.HTTP_404_NOT_FOUND)
                if action == "add":
                    SessionEnrollment.objects.get_or_create(
                        tenant=tenant, enrollment=enrollment, session_id=hw.session_id,
                    )
                    HomeworkAssignment.objects.get_or_create(
                        tenant=tenant,
                        homework_id=hw.id,
                        session_id=hw.session_id,
                        enrollment=enrollment,
                    )
                else:
                    HomeworkAssignment.objects.filter(
                        tenant=tenant,
                        homework_id=hw.id,
                        session_id=hw.session_id,
                        enrollment=enrollment,
                    ).delete()

        return Response({"ok": True, "target_type": target_type, "target_id": target_id, "action": action})
