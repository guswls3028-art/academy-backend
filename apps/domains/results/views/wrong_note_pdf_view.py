# apps/domains/results/views/wrong_note_pdf_view.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from apps.domains.results.permissions import is_teacher_user
from apps.domains.enrollment.models import Enrollment
from apps.domains.results.models.wrong_note_pdf import WrongNotePDF

from apps.domains.results.tasks.wrong_note_pdf_tasks import (
    generate_wrong_note_pdf_task,
)


class WrongNotePDFCreateView(APIView):
    """
    오답노트 PDF 생성 요청

    🔴 보안 패치:
    - 학생은 본인 enrollment만 PDF 생성 가능
    - 교사/관리자는 전체 허용
    """

    permission_classes = [IsAuthenticated]

    def _assert_enrollment_access(self, request, enrollment_id: int) -> None:
        user = request.user

        if is_teacher_user(user):
            return

        qs = Enrollment.objects.filter(id=int(enrollment_id))
        if hasattr(Enrollment, "user_id"):
            qs = qs.filter(user_id=user.id)
        elif hasattr(Enrollment, "student_id"):
            qs = qs.filter(student_id=user.id)

        if not qs.exists():
            raise PermissionDenied("You cannot create PDF for this enrollment_id.")

    def post(self, request):
        enrollment_id = request.data.get("enrollment_id")
        if not enrollment_id:
            return Response({"detail": "enrollment_id required"}, status=400)

        # 🔐 접근 권한 검사
        self._assert_enrollment_access(request, int(enrollment_id))

        lecture_id = request.data.get("lecture_id")
        exam_id = request.data.get("exam_id")
        from_order = request.data.get("from_session_order", 2)

        job = WrongNotePDF.objects.create(
            enrollment_id=int(enrollment_id),
            lecture_id=lecture_id,
            exam_id=exam_id,
            from_session_order=int(from_order),
        )

        generate_wrong_note_pdf_task.delay(job.id)

        return Response({
            "job_id": job.id,
            "status": job.status,
        })
