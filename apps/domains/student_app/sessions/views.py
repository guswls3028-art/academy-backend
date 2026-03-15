# apps/domains/student_app/sessions/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.lectures.models import Session as LectureSession
from apps.domains.clinic.models import SessionParticipant
from .serializers import StudentSessionSerializer


class StudentSessionListView(APIView):
    """
    GET /student/sessions/me/
    학생이 접근 가능한 차시 목록 + 클리닉 예약 (date 기준 정렬).
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        student = get_request_student(request)
        if not student:
            return Response(StudentSessionSerializer([], many=True).data)
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(StudentSessionSerializer([], many=True).data)

        # 1) 강의 차시
        session_ids = (
            SessionEnrollment.objects.filter(
                enrollment__student=student,
                enrollment__tenant=tenant,
                enrollment__status="ACTIVE",
            )
            .values_list("session_id", flat=True)
            .distinct()
        )
        sessions = (
            LectureSession.objects.filter(id__in=session_ids)
            .select_related("lecture")
            .order_by("date", "order", "id")
        )
        data = [
            {
                "id": s.id,
                "title": getattr(s, "title", "") or f"{getattr(s.lecture, 'title', '')} {s.order}차시",
                "date": s.date.isoformat() if s.date else None,
                "status": None,
                "exam_ids": [],
                "type": "session",
            }
            for s in sessions
        ]

        # 2) 클리닉 예약 (PENDING/BOOKED만, session 있는 것만)
        clinic_participants = (
            SessionParticipant.objects
            .filter(
                student=student,
                tenant=tenant,
                status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
                session__isnull=False,
            )
            .select_related("session")
        )
        for cp in clinic_participants:
            sess = cp.session
            status_label = "대기 중" if cp.status == "pending" else "예약됨"
            data.append({
                "id": cp.id * -1,  # 음수 ID로 클리닉 구분
                "title": f"🏥 클리닉 {sess.title or sess.location}" if sess else "🏥 클리닉",
                "date": sess.date.isoformat() if sess and sess.date else None,
                "status": status_label,
                "exam_ids": [],
                "type": "clinic",
            })

        # 날짜 정렬
        data.sort(key=lambda x: x.get("date") or "9999-99-99")
        return Response(StudentSessionSerializer(data, many=True).data)


class StudentSessionDetailView(APIView):
    """
    GET /student/sessions/{id}/
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, pk):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "Not found."}, status=404)
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Not found."}, status=404)
        has_access = SessionEnrollment.objects.filter(
            enrollment__student=student,
            enrollment__tenant=tenant,
            enrollment__status="ACTIVE",  # ✅ 퇴원 학생 제외
            session_id=pk,
        ).exists()
        if not has_access:
            return Response({"detail": "Not found."}, status=404)
        session = LectureSession.objects.filter(id=pk).select_related("lecture").first()
        if not session:
            return Response({"detail": "Not found."}, status=404)
        data = {
            "id": session.id,
            "title": getattr(session, "title", "") or f"{getattr(session.lecture, 'title', '')} {session.order}차시",
            "date": session.date.isoformat() if session.date else None,
            "status": None,
            "exam_ids": [],
        }
        return Response(StudentSessionSerializer(data).data)
