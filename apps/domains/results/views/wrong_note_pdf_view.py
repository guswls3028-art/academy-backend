# apps/domains/results/views/wrong_note_pdf_view.py
from __future__ import annotations

from django.urls import reverse

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from apps.domains.results.permissions import is_teacher_user
from apps.domains.enrollment.models import Enrollment
from apps.domains.results.models.wrong_note_pdf import WrongNotePDF


class WrongNotePDFCreateView(APIView):
    """
    오답노트 PDF 생성 요청

    ✅ STEP 3-2.5:
    - 응답에 status_url 포함 (프론트 폴링 편의)

    ✅ STEP 2 (중요):
    - View 파일 상단에서 Celery task import 금지
      (URLConf import 시점에 worker 의존성까지 로딩되는 위험 방지)
    - 따라서 post() 내부에서만 지연 import 한다.
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

        enrollment_id_i = int(enrollment_id)
        self._assert_enrollment_access(request, enrollment_id_i)

        lecture_id = request.data.get("lecture_id")
        exam_id = request.data.get("exam_id")
        from_order = request.data.get("from_session_order", 2)

        job = WrongNotePDF.objects.create(
            enrollment_id=enrollment_id_i,
            lecture_id=int(lecture_id) if lecture_id else None,
            exam_id=int(exam_id) if exam_id else None,
            from_session_order=int(from_order or 2),
        )

        # --------------------------------------------------
        # ✅ STEP 2: task 지연 import (중요)
        # - URL import 시점에 worker 쪽 의존성을 끌어오지 않음
        # --------------------------------------------------
        from apps.domains.results.tasks.wrong_note_pdf_tasks import (
            generate_wrong_note_pdf_task,
        )

        generate_wrong_note_pdf_task.delay(job.id)

        # ✅ status_url 제공 (절대경로)
        status_path = reverse("wrong-note-pdf-status", kwargs={"job_id": job.id})
        status_url = request.build_absolute_uri(status_path)

        return Response({
            "job_id": job.id,
            "status": job.status,
            "status_url": status_url,
        })
