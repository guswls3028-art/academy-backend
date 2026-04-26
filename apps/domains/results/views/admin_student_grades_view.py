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
from apps.domains.results.models import Result
from apps.domains.exams.models import Exam
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.exam_achievement import compute_exam_achievement_bulk
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

        exams_map = {}
        if exam_ids:
            # 🔐 tenant 강제 — Exam.tenant FK 존재.
            for e in Exam.objects.filter(id__in=exam_ids, tenant=tenant).only("id", "title", "pass_score"):
                exams_map[e.id] = {"title": e.title, "pass_score": float(e.pass_score or 0)}

        # 재시도 횟수 (bulk)
        retake_counts = {}
        if exam_ids and enrollment_ids:
            from django.db.models import Max
            from apps.domains.results.models import ExamAttempt as _EA
            for att in _EA.objects.filter(
                exam_id__in=exam_ids,
                enrollment_id__in=enrollment_ids,
            ).values("exam_id", "enrollment_id").annotate(max_attempt=Max("attempt_index")):
                retake_counts[(att["enrollment_id"], att["exam_id"])] = att["max_attempt"]

        # 세션/강의 정보 — enrollment → lecture 매핑
        # 🔐 enrollment_ids는 line 44에서 이미 tenant 필터됨. 명시적으로 한 번 더.
        enrollment_lecture_map = {}
        if enrollment_ids:
            for en in Enrollment.objects.filter(id__in=enrollment_ids, tenant=tenant).select_related("lecture").only("id", "lecture__id", "lecture__title", "lecture__color", "lecture__chip_label"):
                enrollment_lecture_map[en.id] = {
                    "lecture_id": en.lecture_id,
                    "lecture_title": en.lecture.title if en.lecture else None,
                    "lecture_color": getattr(en.lecture, "color", None),
                    "lecture_chip_label": getattr(en.lecture, "chip_label", None),
                }

        # ── Stage 1: exam 단위 dedup + session lookup + 시스템 강의 스킵
        exam_rows = []  # [(r, eid, info, session, lecture_meta)]
        seen_exam_ids = set()
        for r in results:
            eid = r["target_id"]
            if eid in seen_exam_ids:
                continue
            seen_exam_ids.add(eid)
            info = exams_map.get(eid) or {"title": f"시험 #{eid}", "pass_score": 0}
            session = get_primary_session_for_exam(eid)

            # 시스템 강의(공개 영상 컨테이너)는 성적에서 제외
            if session and hasattr(session, "lecture") and session.lecture \
               and getattr(session.lecture, "is_system", False):
                continue

            session_id = session.id if session else None
            session_title = (
                getattr(session, "title", None) or f"{getattr(session, 'order', '')}차시"
            ) if session else None
            lecture_id = getattr(session, "lecture_id", None) if session else None
            lecture_title = getattr(getattr(session, "lecture", None), "title", None) if session else None
            lecture_color = getattr(getattr(session, "lecture", None), "color", None) if session else None
            lecture_chip_label = getattr(getattr(session, "lecture", None), "chip_label", None) if session else None

            # enrollment → lecture fallback
            if not lecture_title:
                enroll_info = enrollment_lecture_map.get(r["enrollment_id"])
                if enroll_info:
                    lecture_id = lecture_id or enroll_info["lecture_id"]
                    lecture_title = lecture_title or enroll_info["lecture_title"]
                    lecture_color = lecture_color or enroll_info["lecture_color"]
                    lecture_chip_label = lecture_chip_label or enroll_info["lecture_chip_label"]

            exam_rows.append({
                "r": r, "eid": eid, "info": info, "session": session,
                "session_id": session_id, "session_title": session_title,
                "lecture_id": lecture_id, "lecture_title": lecture_title,
                "lecture_color": lecture_color, "lecture_chip_label": lecture_chip_label,
            })

        # ── Stage 2: SSOT 유틸로 성취 일괄 계산 (ClinicLink/ExamAttempt/ExamResult 각 1쿼리)
        bulk_items = [
            {
                "enrollment_id": row["r"]["enrollment_id"],
                "exam_id": row["eid"],
                "total_score": row["r"]["total_score"],
                "pass_score": row["info"]["pass_score"],
                "attempt_id": row["r"].get("attempt_id"),
                "session": row["session"],
            }
            for row in exam_rows
        ]
        # admin_student_grades_view 는 exam 별로 대표 session 을 이미 선택했으므로
        # (동일 exam 이 여러 session 에 걸려도 primary 만 사용), session-agnostic 매칭.
        # 정책 동등성(HOMEWORK_PASS 케이스)은 test_achievement_contract.py 가 보장.
        bulk_ach = compute_exam_achievement_bulk(
            items=bulk_items, use_session_filter=False,
        )

        # ── Stage 3: 응답 row 조립
        exam_list = []
        for row in exam_rows:
            r = row["r"]
            eid = row["eid"]
            info = row["info"]
            enroll_id = r["enrollment_id"]
            ach_data = bulk_ach.get((int(enroll_id), int(eid)), {})
            meta_status = ach_data.get("meta_status")
            is_not_submitted = meta_status == "NOT_SUBMITTED"
            max_attempt = retake_counts.get((enroll_id, eid), 1)

            exam_list.append({
                "exam_id": eid,
                "enrollment_id": enroll_id,
                "title": info["title"],
                "total_score": None if is_not_submitted else r["total_score"],
                "max_score": r["max_score"],
                "is_pass": ach_data.get("is_pass"),
                "achievement": ach_data.get("achievement"),
                "meta_status": meta_status,
                "retake_count": max_attempt,
                "session_id": row["session_id"],
                "session_title": row["session_title"],
                "lecture_id": row["lecture_id"],
                "lecture_title": row["lecture_title"],
                "lecture_color": row["lecture_color"],
                "lecture_chip_label": row["lecture_chip_label"],
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
