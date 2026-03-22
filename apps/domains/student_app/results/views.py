# apps/domains/student_app/results/views.py
"""
GET /student/results/me/exams/<exam_id>/ 및 /items/
→ results 도메인 단일 진실(get_my_exam_result_data) 사용.

GET /student/grades/
→ 학생 본인 시험 결과 목록 + 과제 성적 목록 (기입된 성적만).
"""
from django.http import Http404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from apps.domains.results.services.student_result_service import get_my_exam_result_data
from apps.domains.enrollment.models import Enrollment
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.exams.models import Exam
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.homework_results.models import HomeworkScore
from apps.domains.progress.models import ClinicLink


class MyExamResultView(APIView):
    """
    GET /student/results/me/exams/{exam_id}/
    결과 도메인 Result 기준 실제 채점 데이터 반환.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, exam_id):
        try:
            data = get_my_exam_result_data(request, int(exam_id), tenant=request.tenant)
        except Http404:
            return Response({"detail": "result not found"}, status=404)
        return Response(data)


class MyExamResultItemsView(APIView):
    """
    GET /student/results/me/exams/{exam_id}/items/
    동일 데이터의 items 배열만 반환 (프론트 문항별 결과 조회).
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, exam_id):
        try:
            data = get_my_exam_result_data(request, int(exam_id), tenant=request.tenant)
        except Http404:
            return Response({"detail": "result not found"}, status=404)
        return Response({"items": data.get("items") or []})


class MyGradesSummaryView(APIView):
    """
    GET /student/grades/
    학생 본인에 대해 기입된 시험 결과 목록 + 과제 성적 목록 반환.
    학생앱 성적 탭에서 시험 결과/과제 이력 카드에 사용.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "student not found"}, status=403)

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"exams": [], "homeworks": []})
        enrollment_ids = list(
            Enrollment.objects.filter(
                student=student,
                tenant=tenant,
            ).values_list("id", flat=True)
        )
        if not enrollment_ids:
            return Response({"exams": [], "homeworks": []})

        # 시험 결과: Result (target_type=exam) → Exam 제목, 합격선, 세션/강의명
        results = list(
            Result.objects.filter(
                enrollment_id__in=enrollment_ids,
                target_type="exam",
            )
            .order_by("-submitted_at")
            .values("target_id", "enrollment_id", "total_score", "max_score", "submitted_at")
        )
        exam_ids = list({r["target_id"] for r in results})
        exams_map = {}
        if exam_ids:
            for e in Exam.objects.filter(id__in=exam_ids).only("id", "title", "pass_score"):
                exams_map[e.id] = {"title": e.title, "pass_score": float(e.pass_score or 0)}

        # ✅ 클리닉 해소 여부: 1차 불합격이지만 보강으로 최종 합격한 시험 추적
        # enrollment_ids × exam_ids에서 해소된 ClinicLink 조회
        resolved_exam_links = {}
        if exam_ids and enrollment_ids:
            for cl in ClinicLink.objects.filter(
                enrollment_id__in=enrollment_ids,
                source_type="exam",
                source_id__in=exam_ids,
                resolved_at__isnull=False,
                resolution_type__in=["EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"],
            ).values("enrollment_id", "source_id", "resolution_type"):
                resolved_exam_links[(cl["enrollment_id"], cl["source_id"])] = cl["resolution_type"]

        # 시험별 재시도 횟수
        retake_counts = {}
        if exam_ids and enrollment_ids:
            from django.db.models import Max
            for att in ExamAttempt.objects.filter(
                exam_id__in=exam_ids,
                enrollment_id__in=enrollment_ids,
            ).values("exam_id", "enrollment_id").annotate(max_attempt=Max("attempt_index")):
                retake_counts[(att["enrollment_id"], att["exam_id"])] = att["max_attempt"]

        exam_list = []
        seen_exam_ids = set()
        for r in results:
            eid = r["target_id"]
            if eid in seen_exam_ids:
                continue
            seen_exam_ids.add(eid)
            info = exams_map.get(eid) or {"title": f"시험 #{eid}", "pass_score": 0}
            session = get_primary_session_for_exam(eid)
            session_title = None
            lecture_title = None
            if session:
                session_title = getattr(session, "title", None) or f"{getattr(session, 'order', '')}차시"
                if hasattr(session, "lecture") and session.lecture:
                    lecture_title = getattr(session.lecture, "title", None)
            # 시스템 강의(공개 영상 컨테이너)는 성적에서 제외
            if session and hasattr(session, "lecture") and session.lecture and getattr(session.lecture, "is_system", False):
                continue

            is_pass_1st = float(r["total_score"]) >= info["pass_score"]
            enroll_id = r["enrollment_id"]
            resolution = resolved_exam_links.get((enroll_id, eid))
            max_attempt = retake_counts.get((enroll_id, eid), 1)

            # 최종 학습 성취 판정
            # - 1차 합격 → "PASS"
            # - 1차 불합격 + 보강 합격 → "REMEDIATED" (보강 후 합격)
            # - 1차 불합격 + 미해소 → "FAIL"
            if is_pass_1st:
                achievement = "PASS"
            elif resolution in ("EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"):
                achievement = "REMEDIATED"
            else:
                achievement = "FAIL"

            exam_list.append({
                "exam_id": eid,
                "enrollment_id": enroll_id,
                "title": info["title"],
                "total_score": r["total_score"],
                "max_score": r["max_score"],
                "is_pass": is_pass_1st,
                "achievement": achievement,
                "retake_count": max_attempt,
                "session_title": session_title,
                "lecture_title": lecture_title,
                "submitted_at": r["submitted_at"].isoformat() if r.get("submitted_at") else None,
            })

        # 과제 성적: HomeworkScore (기입된 것만, score is not None)
        # ✅ 성적 산출: attempt_index=1 (1차) 만 학생에게 표시
        hw_scores = (
            HomeworkScore.objects.filter(enrollment_id__in=enrollment_ids, attempt_index=1)
            .exclude(score__isnull=True)
            .exclude(session__lecture__is_system=True)
            .select_related("homework", "session", "session__lecture")
            .order_by("-updated_at")
        )
        # ✅ 과제 클리닉 해소 추적
        hw_ids = list({hs.homework_id for hs in hw_scores})
        resolved_hw_links = {}
        if hw_ids and enrollment_ids:
            for cl in ClinicLink.objects.filter(
                enrollment_id__in=enrollment_ids,
                source_type="homework",
                source_id__in=hw_ids,
                resolved_at__isnull=False,
                resolution_type__in=["EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"],
            ).values("enrollment_id", "source_id", "resolution_type"):
                resolved_hw_links[(cl["enrollment_id"], cl["source_id"])] = cl["resolution_type"]

        # 과제 재시도 횟수
        hw_retake_counts = {}
        if hw_ids and enrollment_ids:
            from django.db.models import Max as HwMax
            for row in HomeworkScore.objects.filter(
                homework_id__in=hw_ids,
                enrollment_id__in=enrollment_ids,
            ).values("homework_id", "enrollment_id").annotate(max_attempt=HwMax("attempt_index")):
                hw_retake_counts[(row["enrollment_id"], row["homework_id"])] = row["max_attempt"]

        homework_list = []
        seen_hw_key = set()
        for hs in hw_scores:
            key = (hs.homework_id, hs.session_id, hs.enrollment_id)
            if key in seen_hw_key:
                continue
            seen_hw_key.add(key)
            session = hs.session
            session_title = None
            lecture_title = None
            if session:
                session_title = getattr(session, "title", None) or f"{getattr(session, 'order', '')}차시"
                if hasattr(session, "lecture") and session.lecture:
                    lecture_title = getattr(session.lecture, "title", None)

            is_pass_1st = bool(hs.passed)
            resolution = resolved_hw_links.get((hs.enrollment_id, hs.homework_id))
            max_attempt = hw_retake_counts.get((hs.enrollment_id, hs.homework_id), 1)

            if is_pass_1st:
                achievement = "PASS"
            elif resolution in ("EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"):
                achievement = "REMEDIATED"
            else:
                achievement = "FAIL"

            homework_list.append({
                "homework_id": hs.homework_id,
                "enrollment_id": hs.enrollment_id,
                "title": hs.homework.title if hs.homework else f"과제 #{hs.homework_id}",
                "score": hs.score,
                "max_score": hs.max_score,
                "passed": is_pass_1st,
                "achievement": achievement,
                "retake_count": max_attempt,
                "session_title": session_title,
                "lecture_title": lecture_title,
            })

        return Response({
            "exams": exam_list,
            "homeworks": homework_list,
        })
