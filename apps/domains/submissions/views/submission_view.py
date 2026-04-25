# PATH: apps/domains/submissions/views/submission_view.py
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.serializers.submission import (
    SubmissionSerializer,
    SubmissionCreateSerializer,
)
from apps.domains.submissions.services.dispatcher import dispatch_submission
from apps.domains.submissions.services.transition import (
    transit_save,
    InvalidTransitionError,
)
from apps.domains.results.services.grading_service import grade_submission


class SubmissionViewSet(ModelViewSet):
    # 기본: Member (학생 CREATE 허용). 관리자 전용 액션은 get_permissions에서 Staff로 승격.
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    # 학생/학부모에게도 열린 액션 (본인 소유 생성만). 그 외는 Staff-only.
    STUDENT_ALLOWED_ACTIONS = {"create"}

    def get_permissions(self):
        if self.action in self.STUDENT_ALLOWED_ACTIONS:
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return Submission.objects.none()
        qs = Submission.objects.filter(tenant=tenant).order_by("-id")
        # 학생/학부모는 자기 제출만 볼 수 있음 — 프로필 존재로 판별
        # (과거 tenant_role 기반 분기는 해당 플래그가 어디에서도 설정되지 않아 항상 미적용 상태였다.)
        user = self.request.user
        is_student_or_parent = (
            getattr(user, "student_profile", None) is not None
            or getattr(user, "parent_profile", None) is not None
        )
        if is_student_or_parent:
            qs = qs.filter(user=user)
        return qs

    def get_serializer_class(self):
        if self.action in ("create", "admin_omr_upload"):
            return SubmissionCreateSerializer
        return SubmissionSerializer

    @action(detail=False, methods=["post"], url_path="admin/omr-upload")
    def admin_omr_upload(self, request):
        """
        POST /api/v1/submissions/submissions/admin/omr-upload/
        form-data: enrollment_id, target_id (exam_id), file
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)

        enrollment_id = request.data.get("enrollment_id")
        target_id = request.data.get("target_id")
        file_obj = request.FILES.get("file")

        if not target_id:
            return Response({"detail": "target_id (exam_id) required"}, status=400)
        if not file_obj:
            return Response({"detail": "file required"}, status=400)

        try:
            exam_id = int(target_id)
        except (TypeError, ValueError):
            return Response({"detail": "target_id must be an integer"}, status=400)

        payload = {}
        if request.data.get("sheet_id"):
            try:
                payload["sheet_id"] = int(request.data.get("sheet_id"))
            except (TypeError, ValueError):
                pass

        ser = SubmissionCreateSerializer(
            data={
                "enrollment_id": int(enrollment_id) if enrollment_id else None,
                "target_type": Submission.TargetType.EXAM,
                "target_id": exam_id,
                "source": Submission.Source.OMR_SCAN,
                "payload": payload or None,
                "file": file_obj,
            }
        )
        ser.is_valid(raise_exception=True)
        submission = ser.save(user=request.user, tenant=tenant)
        dispatch_submission(submission)

        return Response(
            {"submission_id": submission.id, "status": submission.status},
            status=201,
        )

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return
        # target_id(exam/homework)가 해당 테넌트 소속인지 검증
        target_type = serializer.validated_data.get("target_type")
        target_id = serializer.validated_data.get("target_id")
        if target_type and target_id:
            if not self._validate_target_tenant(target_type, target_id, tenant):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("대상이 해당 학원에 속하지 않습니다.")
        # enrollment_id 소유권 검증: 학생은 자신의 enrollment만 사용 가능
        enrollment_id = serializer.validated_data.get("enrollment_id")
        if enrollment_id:
            student = getattr(self.request.user, "student_profile", None)
            if student:
                from apps.domains.enrollment.models import Enrollment
                if not Enrollment.objects.filter(
                    id=enrollment_id, student=student, tenant=tenant,
                ).exists():
                    from rest_framework.exceptions import PermissionDenied
                    raise PermissionDenied("해당 수강 정보에 접근할 수 없습니다.")
        submission = serializer.save(user=self.request.user, tenant=tenant)
        dispatch_submission(submission)

    @staticmethod
    def _validate_target_tenant(target_type, target_id, tenant) -> bool:
        """target_id가 해당 tenant 소속인지 검증."""
        try:
            if target_type == Submission.TargetType.EXAM:
                from apps.domains.exams.models import Exam
                return Exam.objects.filter(
                    id=int(target_id), sessions__lecture__tenant=tenant,
                ).exists()
            elif target_type == Submission.TargetType.HOMEWORK:
                from apps.domains.homework_results.models import Homework
                return Homework.objects.filter(
                    id=int(target_id),
                    session__lecture__tenant=tenant,
                ).exists()
        except Exception:
            pass
        return False  # 알 수 없는 target_type은 거부 (fail-closed)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        with transaction.atomic():
            submission = Submission.objects.select_for_update().get(pk=self.get_object().pk)

            try:
                transit_save(
                    submission, Submission.Status.SUBMITTED,
                    actor="admin.retry",
                )
            except InvalidTransitionError:
                return Response(
                    {"detail": "Only FAILED submissions can be retried."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        dispatch_submission(submission)

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
            }
        )

    @action(detail=True, methods=["get", "post"], url_path="manual-edit")
    def manual_edit(self, request, pk=None):
        if request.method == "GET":
            return self._manual_edit_get(request, pk)
        return self._manual_edit_post(request, pk)

    def _manual_edit_get(self, request, pk=None):
        """GET: 현재 답안 목록 + identifier + 스캔 이미지 URL 반환 (수동 편집 화면용)."""
        submission: Submission = self.get_object()
        answers_qs = SubmissionAnswer.objects.filter(
            submission=submission,
        ).order_by("exam_question_id")
        answers_data = []
        for a in answers_qs:
            am = a.meta or {}
            omr = am.get("omr") if isinstance(am, dict) else None
            answers_data.append({
                "question_id": a.exam_question_id,
                "question_no": a.exam_question_id,
                "answer": a.answer or "",
                "omr": omr if isinstance(omr, dict) else None,
            })
        meta = dict(submission.meta or {})
        identifier = None
        omr = meta.get("omr") or {}
        if isinstance(omr, dict):
            identifier = omr.get("identifier_override") or omr.get("identifier")

        # ✅ 스캔 이미지 presigned URL (6h TTL — 장시간 검토 세션 대응)
        scan_image_url = ""
        if submission.file_key and submission.source == Submission.Source.OMR_SCAN:
            try:
                from apps.infrastructure.storage.r2 import generate_presigned_get_url
                scan_image_url = generate_presigned_get_url(
                    key=submission.file_key,
                    expires_in=21600,
                )
            except Exception:
                scan_image_url = ""

        return Response({
            "submission_id": submission.id,
            "submission_status": submission.status,
            "enrollment_id": submission.enrollment_id,
            "target_type": submission.target_type,
            "target_id": submission.target_id,
            "identifier": identifier,
            "answers": answers_data,
            "scan_image_url": scan_image_url,
            "meta": {
                "manual_review": meta.get("manual_review"),
                "ai_result": meta.get("ai_result"),
                "identifier_status": meta.get("identifier_status"),
            },
        })

    @transaction.atomic
    def _manual_edit_post(self, request, pk=None):
        submission: Submission = Submission.objects.select_for_update().get(pk=self.get_object().pk)

        identifier = request.data.get("identifier")
        answers = request.data.get("answers") or []
        note = str(request.data.get("note") or "manual_edit")

        # ✅ identifier 검증 + submission.enrollment_id 반영 (tenant 안전성)
        #    { "enrollment_id": N } 형식만 매칭. 이외 형식은 meta로 저장만 하고 enrollment 미반영.
        resolved_enrollment_id: int | None = None
        if isinstance(identifier, dict) and identifier.get("enrollment_id") is not None:
            try:
                candidate_eid = int(identifier["enrollment_id"])
            except (TypeError, ValueError):
                return Response(
                    {"detail": "enrollment_id는 정수여야 합니다."},
                    status=400,
                )

            # 해당 시험의 enrollment 후보인지 검증 (ExamEnrollment → SessionEnrollment fallback)
            from apps.domains.enrollment.models import Enrollment, SessionEnrollment
            from apps.domains.exams.models import ExamEnrollment

            tenant = submission.tenant
            exam_id = int(submission.target_id or 0) if submission.target_type == Submission.TargetType.EXAM else 0

            if not Enrollment.objects.filter(id=candidate_eid, tenant=tenant).exists():
                return Response(
                    {"detail": f"enrollment_id={candidate_eid}는 현재 학원의 학생이 아닙니다."},
                    status=400,
                )

            if exam_id:
                in_exam = ExamEnrollment.objects.filter(
                    exam_id=exam_id, enrollment_id=candidate_eid
                ).exists()
                if not in_exam:
                    # fallback: SessionEnrollment
                    in_session = SessionEnrollment.objects.filter(
                        session__exams__id=exam_id,
                        enrollment_id=candidate_eid,
                    ).exists()
                    if not in_session:
                        return Response(
                            {"detail": "해당 시험에 등록되지 않은 학생입니다."},
                            status=400,
                        )

            # ✅ 중복 매칭 차단 (기본): 같은 시험의 다른 submission이 이미 같은 enrollment로 active면 409.
            #    override=1 쿼리파라미터로만 덮어쓰기 허용 (운영자 명시적 선택).
            allow_duplicate = str(request.query_params.get("allow_duplicate") or "").lower() in ("1", "true", "yes")
            if exam_id and not allow_duplicate:
                dup_qs = (
                    Submission.objects
                    .filter(
                        tenant=tenant,
                        target_type=Submission.TargetType.EXAM,
                        target_id=exam_id,
                        enrollment_id=candidate_eid,
                        status__in=[
                            Submission.Status.ANSWERS_READY,
                            Submission.Status.GRADING,
                            Submission.Status.DONE,
                        ],
                    )
                    .exclude(id=submission.id)
                    .order_by("-id")
                )
                dup = dup_qs.first()
                if dup:
                    return Response(
                        {
                            "detail": "이미 이 학생에 매칭된 답안지가 있습니다. 덮어쓰려면 확인이 필요합니다.",
                            "code": "DUPLICATE_ENROLLMENT",
                            "conflict_submission_id": int(dup.id),
                            "conflict_file_key": dup.file_key or "",
                            "conflict_status": dup.status,
                        },
                        status=409,
                    )

            resolved_enrollment_id = candidate_eid

        # admin_override=True: DONE/FAILED/SUBMITTED/DISPATCHED/NI → ANSWERS_READY 허용
        # GRADING/SUPERSEDED → ANSWERS_READY 차단 (transition.py에서 강제)
        try:
            transit_save(
                submission, Submission.Status.ANSWERS_READY,
                admin_override=True,
                actor=f"admin.manual_edit.user_{getattr(request.user, 'id', '?')}",
            )
        except InvalidTransitionError as e:
            return Response({"detail": str(e)}, status=409)

        updated = 0

        for a in answers:
            if not isinstance(a, dict):
                continue

            eqid = a.get("exam_question_id")
            if not eqid:
                continue

            ans = str(a.get("answer") or "")

            SubmissionAnswer.objects.update_or_create(
                submission=submission,
                exam_question_id=int(eqid),
                defaults={"answer": ans, "tenant": submission.tenant},
            )
            updated += 1

        meta = dict(submission.meta or {})
        meta.setdefault("omr", {})
        meta["omr"]["identifier_override"] = identifier

        meta.setdefault("manual_edits", [])
        meta["manual_edits"].append(
            {
                "at": timezone.now().isoformat(),
                "by_user_id": getattr(request.user, "id", None),
                "note": note,
                "updated_answers_count": updated,
                "identifier": identifier,
                "resolved_enrollment_id": resolved_enrollment_id,
            }
        )

        meta.setdefault("manual_review", {})
        meta["manual_review"]["required"] = False
        meta["manual_review"]["resolved_at"] = timezone.now().isoformat()

        # ✅ 검증된 enrollment_id가 있으면 submission에 반영 (+ identifier_status matched)
        save_fields = ["meta", "updated_at"]
        if resolved_enrollment_id is not None:
            submission.enrollment_id = resolved_enrollment_id
            save_fields.append("enrollment_id")
            meta["identifier_status"] = "matched"

        submission.meta = meta
        submission.save(update_fields=save_fields)

        try:
            result_obj = grade_submission(int(submission.id))
        except Exception:
            return Response(
                {
                    "submission_id": submission.id,
                    "status": submission.status,
                    "updated": updated,
                    "detail": "grading failed",
                },
                status=500,
            )

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
                "updated": updated,
                "graded": True,
                "result_id": getattr(result_obj, "id", None),
                "resolved_enrollment_id": resolved_enrollment_id,
            }
        )

    @action(detail=True, methods=["post"], url_path="discard")
    def discard(self, request, pk=None):
        """
        OMR 답안지 폐기 — 스캔 품질 불량/오업로드/중복 등으로 채점 대상에서 제외.
        - 상태 FAILED로 전환하고 meta.discarded 기록 (감사 목적).
        - 본 제출에 매칭된 enrollment_id는 유지(기록용)하되 채점 미시행.
        body (optional): {"reason": "scan_quality"}
        """
        submission: Submission = self.get_object()
        reason = str(request.data.get("reason") or "operator_discarded").strip() or "operator_discarded"

        try:
            transit_save(
                submission, Submission.Status.FAILED,
                admin_override=True,
                error_message=f"discarded:{reason}",
                actor=f"admin.discard.user_{getattr(request.user, 'id', '?')}",
            )
        except InvalidTransitionError as e:
            return Response({"detail": str(e)}, status=409)

        meta = dict(submission.meta or {})
        meta["discarded"] = {
            "at": timezone.now().isoformat(),
            "by_user_id": getattr(request.user, "id", None),
            "reason": reason,
        }
        meta.setdefault("manual_review", {})
        meta["manual_review"]["required"] = False
        meta["manual_review"]["resolved_at"] = timezone.now().isoformat()
        submission.meta = meta
        submission.save(update_fields=["meta", "updated_at"])

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
                "discarded": True,
                "reason": reason,
            }
        )
