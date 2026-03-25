# PATH: apps/domains/results/views/admin_student_grades_view.py
"""
GET /results/admin/student-grades/?student_id=<int>

Admin/Teacher용 학생 개인 성적 요약 — 학생 상세 오버레이에서 사용.
student_app.results.MyGradesSummaryView와 동일 로직, admin 컨텍스트.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.enrollment.models import Enrollment
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.exams.models import Exam
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.homework_results.models import HomeworkScore
from apps.domains.progress.models import ClinicLink
from apps.domains.students.models import Student


class AdminStudentGradesView(APIView):
    """
    학생 개인 시험 + 과제 성적 요약 (admin 전용).
    Query params: student_id (required)
    """
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request):
        student_id = request.query_params.get("student_id")
        if not student_id:
            return Response({"detail": "student_id is required"}, status=400)

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"exams": [], "homeworks": []})

        # tenant isolation: 해당 테넌트의 학생인지 확인
        if not Student.objects.filter(id=int(student_id), tenant=tenant).exists():
            return Response({"detail": "student not found"}, status=404)

        enrollment_ids = list(
            Enrollment.objects.filter(
                student_id=int(student_id),
                tenant=tenant,
            ).values_list("id", flat=True)
        )
        if not enrollment_ids:
            return Response({"exams": [], "homeworks": []})

        # ── 시험 결과 ──
        results = list(
            Result.objects.filter(
                enrollment_id__in=enrollment_ids,
                target_type="exam",
            )
            .order_by("-submitted_at")
            .values("target_id", "enrollment_id", "total_score", "max_score", "submitted_at", "attempt_id")
        )
        exam_ids = list({r["target_id"] for r in results})

        # ✅ 미응시 감지: ExamAttempt.meta.status
        _attempt_ids = {int(r["attempt_id"]) for r in results if r.get("attempt_id")}
        _attempt_meta_map = {}
        if _attempt_ids:
            for a in ExamAttempt.objects.filter(id__in=_attempt_ids).only("id", "meta"):
                _attempt_meta_map[int(a.id)] = (a.meta or {}).get("status")
        exams_map = {}
        if exam_ids:
            for e in Exam.objects.filter(id__in=exam_ids).only("id", "title", "pass_score"):
                exams_map[e.id] = {"title": e.title, "pass_score": float(e.pass_score or 0)}

        # 클리닉 해소 여부
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

        # 재시도 횟수
        retake_counts = {}
        if exam_ids and enrollment_ids:
            from django.db.models import Max
            for att in ExamAttempt.objects.filter(
                exam_id__in=exam_ids,
                enrollment_id__in=enrollment_ids,
            ).values("exam_id", "enrollment_id").annotate(max_attempt=Max("attempt_index")):
                retake_counts[(att["enrollment_id"], att["exam_id"])] = att["max_attempt"]

        # 세션/강의 정보 — enrollment → lecture 매핑
        enrollment_lecture_map = {}
        if enrollment_ids:
            for en in Enrollment.objects.filter(id__in=enrollment_ids).select_related("lecture").only("id", "lecture__id", "lecture__title", "lecture__color", "lecture__chip_label"):
                enrollment_lecture_map[en.id] = {
                    "lecture_id": en.lecture_id,
                    "lecture_title": en.lecture.title if en.lecture else None,
                    "lecture_color": getattr(en.lecture, "color", None),
                    "lecture_chip_label": getattr(en.lecture, "chip_label", None),
                }

        exam_list = []
        seen_exam_ids = set()
        for r in results:
            eid = r["target_id"]
            if eid in seen_exam_ids:
                continue
            seen_exam_ids.add(eid)
            info = exams_map.get(eid) or {"title": f"시험 #{eid}", "pass_score": 0}
            session = get_primary_session_for_exam(eid)
            session_id = None
            session_title = None
            lecture_id = None
            lecture_title = None
            lecture_color = None
            lecture_chip_label = None
            if session:
                session_id = session.id
                session_title = getattr(session, "title", None) or f"{getattr(session, 'order', '')}차시"
                if hasattr(session, "lecture") and session.lecture:
                    lecture_id = session.lecture_id
                    lecture_title = getattr(session.lecture, "title", None)
                    lecture_color = getattr(session.lecture, "color", None)
                    lecture_chip_label = getattr(session.lecture, "chip_label", None)

            # 시스템 강의(공개 영상 컨테이너)는 성적에서 제외
            if session and hasattr(session, "lecture") and session.lecture and getattr(session.lecture, "is_system", False):
                continue

            # enrollment → lecture fallback
            if not lecture_title:
                enroll_info = enrollment_lecture_map.get(r["enrollment_id"])
                if enroll_info:
                    lecture_id = lecture_id or enroll_info["lecture_id"]
                    lecture_title = lecture_title or enroll_info["lecture_title"]
                    lecture_color = lecture_color or enroll_info["lecture_color"]
                    lecture_chip_label = lecture_chip_label or enroll_info["lecture_chip_label"]

            _meta_status = _attempt_meta_map.get(int(r["attempt_id"])) if r.get("attempt_id") else None
            is_not_submitted = (_meta_status == "NOT_SUBMITTED")

            raw_pass_score = info["pass_score"] or 0
            if is_not_submitted:
                is_pass_1st = None
            elif raw_pass_score > 0:
                is_pass_1st = float(r["total_score"]) >= raw_pass_score
            else:
                is_pass_1st = None
            enroll_id = r["enrollment_id"]
            resolution = resolved_exam_links.get((enroll_id, eid))
            max_attempt = retake_counts.get((enroll_id, eid), 1)

            if is_not_submitted:
                achievement = "NOT_SUBMITTED"
            elif is_pass_1st is None:
                achievement = None
            elif is_pass_1st:
                achievement = "PASS"
            elif resolution in ("EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"):
                achievement = "REMEDIATED"
            else:
                achievement = "FAIL"

            exam_list.append({
                "exam_id": eid,
                "enrollment_id": enroll_id,
                "title": info["title"],
                "total_score": None if is_not_submitted else r["total_score"],
                "max_score": r["max_score"],
                "is_pass": is_pass_1st,
                "achievement": achievement,
                "meta_status": _meta_status,
                "retake_count": max_attempt,
                "session_id": session_id,
                "session_title": session_title,
                "lecture_id": lecture_id,
                "lecture_title": lecture_title,
                "lecture_color": lecture_color,
                "lecture_chip_label": lecture_chip_label,
                "submitted_at": r["submitted_at"].isoformat() if r.get("submitted_at") else None,
            })

        # ── 과제 성적 ──
        hw_scores = (
            HomeworkScore.objects.filter(enrollment_id__in=enrollment_ids, attempt_index=1)
            .exclude(score__isnull=True)
            .exclude(session__lecture__is_system=True)
            .select_related("homework", "session", "session__lecture")
            .order_by("-updated_at")
        )
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
            session_id_hw = None
            session_title = None
            lecture_id_hw = None
            lecture_title = None
            lecture_color = None
            lecture_chip_label = None
            if session:
                session_id_hw = session.id
                session_title = getattr(session, "title", None) or f"{getattr(session, 'order', '')}차시"
                if hasattr(session, "lecture") and session.lecture:
                    lecture_id_hw = session.lecture_id
                    lecture_title = getattr(session.lecture, "title", None)
                    lecture_color = getattr(session.lecture, "color", None)
                    lecture_chip_label = getattr(session.lecture, "chip_label", None)

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
                "session_id": session_id_hw,
                "session_title": session_title,
                "lecture_id": lecture_id_hw,
                "lecture_title": lecture_title,
                "lecture_color": lecture_color,
                "lecture_chip_label": lecture_chip_label,
            })

        return Response({
            "exams": exam_list,
            "homeworks": homework_list,
        })
