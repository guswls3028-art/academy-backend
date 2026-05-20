# PATH: apps/domains/results/views/wrong_note_pdf_view.py
from __future__ import annotations

from django.urls import reverse

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.permissions import TenantResolvedAndMember, is_effective_staff
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam
from apps.domains.lectures.models import Lecture
from apps.domains.results.models.wrong_note_pdf import WrongNotePDF


class WrongNotePDFCreateView(APIView):
    """
    오답노트 PDF 생성 요청 (Celery 제거 → HTTP worker pull/push)

    ✅ 상태값(모델 enum) 단일화:
    - PENDING -> RUNNING -> DONE/FAILED

    응답:
    {
      "job_id": 1,
      "status": "PENDING",
      "status_url": "https://.../results/wrong-notes/pdf/1/"
    }
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def _get_allowed_enrollment(self, request, enrollment_id: int) -> Enrollment:
        user = request.user

        # ✅ tenant isolation: always verify enrollment belongs to tenant
        qs = Enrollment.objects.filter(id=int(enrollment_id), tenant=request.tenant)
        enrollment = qs.select_related("student", "lecture").first()
        if not enrollment:
            raise PermissionDenied("You cannot create PDF for this enrollment_id.")

        if is_effective_staff(user, request.tenant):
            return enrollment

        # Enrollment.student_id는 Student.pk이므로 user.pk 비교는 오매칭 버그.
        student = getattr(user, "student_profile", None)
        if not student:
            raise PermissionDenied("You cannot create PDF for this enrollment_id.")
        if enrollment.student_id != student.id:
            raise PermissionDenied("You cannot create PDF for this enrollment_id.")
        return enrollment

    def _validate_scope_ids(self, request, enrollment: Enrollment) -> tuple[int | None, int | None]:
        lecture_id = request.data.get("lecture_id")
        exam_id = request.data.get("exam_id")

        lecture_id_i = int(lecture_id) if lecture_id else None
        if lecture_id_i is not None:
            if lecture_id_i != enrollment.lecture_id:
                raise ValidationError({"lecture_id": "수강 등록의 강의와 일치하지 않습니다."})
            if not Lecture.objects.filter(id=lecture_id_i, tenant=request.tenant).exists():
                raise ValidationError({"lecture_id": "해당 강의를 찾을 수 없습니다."})

        exam_id_i = int(exam_id) if exam_id else None
        if exam_id_i is not None:
            exam = Exam.objects.filter(id=exam_id_i, tenant=request.tenant).first()
            if not exam:
                raise ValidationError({"exam_id": "해당 시험을 찾을 수 없습니다."})
            if not exam.sessions.filter(lecture_id=enrollment.lecture_id).exists():
                raise ValidationError({"exam_id": "수강 등록의 강의에 연결된 시험만 선택할 수 있습니다."})

        return lecture_id_i, exam_id_i

    def post(self, request):
        enrollment_id = request.data.get("enrollment_id")
        if not enrollment_id:
            return Response({"detail": "enrollment_id required"}, status=400)

        try:
            enrollment_id_i = int(enrollment_id)
            from_order = int(request.data.get("from_session_order", 2) or 2)
            if from_order < 1:
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError({"detail": "enrollment_id/from_session_order must be valid integers."})

        enrollment = self._get_allowed_enrollment(request, enrollment_id_i)
        try:
            lecture_id_i, exam_id_i = self._validate_scope_ids(request, enrollment)
        except ValueError:
            raise ValidationError({"detail": "lecture_id/exam_id must be valid integers."})

        job = WrongNotePDF.objects.create(
            enrollment_id=enrollment_id_i,
            lecture_id=lecture_id_i,
            exam_id=exam_id_i,
            from_session_order=from_order,
            status=WrongNotePDF.Status.PENDING,  # ✅ enqueue = PENDING
        )

        status_path = reverse("wrong-note-pdf-status", kwargs={"job_id": job.id})
        status_url = request.build_absolute_uri(status_path)

        return Response({
            "job_id": int(job.id),
            "status": str(getattr(job, "status", WrongNotePDF.Status.PENDING)),
            "status_url": status_url,
        })
