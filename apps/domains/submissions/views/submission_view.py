# PATH: apps/domains/submissions/views/submission_view.py
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.api.common.upload_validation import (
    DEFAULT_MAX_OMR_SIZE,
    OMR_CONTENT_TYPES,
    OMR_EXTENSIONS,
    validate_uploaded_file,
)
from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.serializers.submission import (
    SubmissionSerializer,
    SubmissionCreateSerializer,
)
from apps.domains.submissions.services.dispatcher import (
    dispatch_submission,
    resolve_omr_sheet_for_exam,
)
from apps.domains.submissions.services.omr_submission_guards import (
    allow_duplicate_requested,
    duplicate_conflict_payload,
    ensure_exam_enrollment_candidate,
    find_conflicting_exam_submission,
)
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
            validate_uploaded_file(
                file_obj,
                allowed_extensions=OMR_EXTENSIONS,
                allowed_content_types=OMR_CONTENT_TYPES,
                max_size=DEFAULT_MAX_OMR_SIZE,
                label="OMR 파일",
                pdf_single_page=True,
            )
        except ValidationError as e:
            return Response(e.detail, status=400)

        try:
            exam_id = int(target_id)
        except (TypeError, ValueError):
            return Response({"detail": "target_id must be an integer"}, status=400)

        # cross-tenant 방어: 자기 학원 소속 exam_id 인지 검증.
        if not self._validate_target_tenant(Submission.TargetType.EXAM, exam_id, tenant):
            return Response({"detail": "대상이 해당 학원에 속하지 않습니다."}, status=403)
        if enrollment_id:
            try:
                enrollment_id_int = int(enrollment_id)
            except (TypeError, ValueError):
                return Response({"detail": "enrollment_id must be an integer"}, status=400)
            if not ensure_exam_enrollment_candidate(
                tenant=tenant,
                exam_id=exam_id,
                enrollment_id=enrollment_id_int,
            ):
                return Response({"detail": "해당 시험에 등록되지 않은 학생입니다."}, status=403)
        else:
            enrollment_id_int = None

        payload = {}
        if request.data.get("sheet_id"):
            try:
                payload["sheet_id"] = int(request.data.get("sheet_id"))
            except (TypeError, ValueError):
                return Response({"detail": "sheet_id must be integer"}, status=400)
        try:
            sheet = resolve_omr_sheet_for_exam(
                tenant=tenant,
                exam_id=exam_id,
                requested_sheet_id=payload.get("sheet_id"),
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        payload["sheet_id"] = int(sheet.id)

        conflict = find_conflicting_exam_submission(
            tenant=tenant,
            exam_id=exam_id,
            enrollment_id=enrollment_id_int,
        )
        if conflict and not allow_duplicate_requested(request):
            return Response(duplicate_conflict_payload(conflict), status=409)

        ser = SubmissionCreateSerializer(
            data={
                "enrollment_id": enrollment_id_int,
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
        submission.refresh_from_db(fields=["status"])

        return Response(
            {"submission_id": submission.id, "status": submission.status},
            status=201,
        )

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return
        # 🔒 학부모는 시험/과제 제출 권한 없음 (자녀가 본인 폰으로 제출해야 함)
        # parent_profile 존재 = 학부모 토큰. student_profile이 없는 부모 계정은 제출 차단.
        # (학부모-학생 겸용 계정은 student_profile이 있으므로 학생 흐름으로 통과)
        from rest_framework.exceptions import PermissionDenied
        is_parent = getattr(self.request.user, "parent_profile", None) is not None
        is_student = getattr(self.request.user, "student_profile", None) is not None
        if is_parent and not is_student:
            raise PermissionDenied("학부모 계정은 시험/과제 제출 권한이 없습니다.")
        source = serializer.validated_data.get("source")
        if source == Submission.Source.OMR_SCAN:
            from apps.core.permissions import is_effective_staff
            if not is_effective_staff(self.request.user, tenant):
                raise PermissionDenied("OMR 업로드는 운영자만 사용할 수 있습니다.")
        # target_id(exam/homework)가 해당 테넌트 소속인지 검증
        target_type = serializer.validated_data.get("target_type")
        target_id = serializer.validated_data.get("target_id")
        if target_type and target_id:
            if not self._validate_target_tenant(target_type, target_id, tenant):
                raise PermissionDenied("대상이 해당 학원에 속하지 않습니다.")
        # enrollment_id 소유권 검증: 학생은 자신의 enrollment만 사용 가능
        enrollment_id = serializer.validated_data.get("enrollment_id")
        if enrollment_id:
            from apps.domains.enrollment.models import Enrollment
            if not Enrollment.objects.filter(id=enrollment_id, tenant=tenant).exists():
                raise PermissionDenied("해당 수강 정보에 접근할 수 없습니다.")
            student = getattr(self.request.user, "student_profile", None)
            if student:
                if not Enrollment.objects.filter(
                    id=enrollment_id, student=student, tenant=tenant,
                ).exists():
                    raise PermissionDenied("해당 수강 정보에 접근할 수 없습니다.")
            if target_type and target_id and not self._validate_target_enrollment_assignment(
                target_type,
                target_id,
                enrollment_id,
                tenant,
                ensure_exam_enrollment=True,
            ):
                raise PermissionDenied("해당 시험/과제에 등록되지 않은 수강 정보입니다.")
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

    @staticmethod
    def _validate_target_enrollment_assignment(
        target_type,
        target_id,
        enrollment_id,
        tenant,
        *,
        ensure_exam_enrollment: bool = False,
    ) -> bool:
        """target과 enrollment가 같은 수업 배정 맥락인지 검증."""
        try:
            target_id_i = int(target_id)
            enrollment_id_i = int(enrollment_id)
        except (TypeError, ValueError):
            return False

        from apps.domains.enrollment.models import Enrollment, SessionEnrollment

        enrollment = (
            Enrollment.objects
            .filter(
                id=enrollment_id_i,
                tenant=tenant,
                status="ACTIVE",
                student__deleted_at__isnull=True,
            )
            .select_related("lecture")
            .first()
        )
        if not enrollment:
            return False

        if target_type == Submission.TargetType.EXAM:
            from apps.domains.exams.models import ExamEnrollment

            in_exam = ExamEnrollment.objects.filter(
                exam_id=target_id_i,
                enrollment_id=enrollment_id_i,
                enrollment__tenant=tenant,
            ).exists()
            if in_exam:
                return True

            in_session = SessionEnrollment.objects.filter(
                tenant=tenant,
                session__exams__id=target_id_i,
                enrollment_id=enrollment_id_i,
                enrollment__status="ACTIVE",
                enrollment__student__deleted_at__isnull=True,
            ).exists()
            if in_session and ensure_exam_enrollment:
                ExamEnrollment.objects.get_or_create(
                    exam_id=target_id_i,
                    enrollment_id=enrollment_id_i,
                )
            return in_session

        if target_type == Submission.TargetType.HOMEWORK:
            from apps.domains.homework_results.models import Homework

            return Homework.objects.filter(
                id=target_id_i,
                session__lecture_id=enrollment.lecture_id,
                session__lecture__tenant=tenant,
            ).exists()

        return False

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
        tenant = submission.tenant

        # ✅ identifier 검증 + submission.enrollment_id 반영 (tenant 안전성)
        #    { "enrollment_id": N } 형식만 매칭. 이외 형식은 meta로 저장만 하고 enrollment 미반영.
        resolved_enrollment_id: int | None = None
        exam_enrollment_created = False
        should_create_exam_enrollment = False
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
                        tenant=tenant,
                        session__exams__id=exam_id,
                        enrollment_id=candidate_eid,
                        enrollment__status="ACTIVE",
                        enrollment__student__deleted_at__isnull=True,
                    ).exists()
                    if not in_session:
                        return Response(
                            {"detail": "해당 시험에 등록되지 않은 학생입니다."},
                            status=400,
                        )
                    should_create_exam_enrollment = True

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
            if should_create_exam_enrollment:
                _, exam_enrollment_created = ExamEnrollment.objects.get_or_create(
                    exam_id=exam_id,
                    enrollment_id=candidate_eid,
                )

        if (
            submission.status == Submission.Status.NEEDS_IDENTIFICATION
            and not submission.enrollment_id
            and resolved_enrollment_id is None
        ):
            return Response(
                {"detail": "학생 식별이 필요한 답안지는 enrollment_id 매칭 후 저장할 수 있습니다."},
                status=400,
            )

        validated_answers: list[tuple[int, str]] = []
        if answers:
            if submission.target_type != Submission.TargetType.EXAM:
                return Response({"detail": "시험 제출 답안만 수동 수정할 수 있습니다."}, status=400)

            from apps.domains.exams.models import Exam, ExamQuestion

            exam = (
                Exam.objects
                .filter(id=int(submission.target_id or 0), tenant=tenant)
                .select_related("template_exam")
                .first()
            )
            if not exam:
                return Response({"detail": "시험을 찾을 수 없습니다."}, status=400)

            sheet_exam_ids = [exam.id]
            if exam.template_exam_id:
                sheet_exam_ids.append(exam.template_exam_id)
            allowed_question_ids = set(
                ExamQuestion.objects.filter(
                    sheet__exam_id__in=sheet_exam_ids,
                    sheet__exam__tenant=tenant,
                ).values_list("id", flat=True)
            )
            if not allowed_question_ids:
                return Response({"detail": "수동 수정 가능한 시험 문항이 없습니다."}, status=400)

            for a in answers:
                if not isinstance(a, dict):
                    continue
                raw_eqid = a.get("exam_question_id") or a.get("question_id")
                if not raw_eqid:
                    continue
                try:
                    eqid = int(raw_eqid)
                except (TypeError, ValueError):
                    return Response({"detail": "question_id는 정수여야 합니다."}, status=400)
                if eqid not in allowed_question_ids:
                    return Response({"detail": "해당 시험의 문항만 수정할 수 있습니다."}, status=400)
                validated_answers.append((eqid, str(a.get("answer") or "")))

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

        for eqid, ans in validated_answers:
            SubmissionAnswer.objects.update_or_create(
                submission=submission,
                exam_question_id=eqid,
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
                "exam_enrollment_created": exam_enrollment_created,
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
            transaction.set_rollback(True)
            return Response(
                {
                    "submission_id": submission.id,
                    "status": submission.status,
                    "updated": updated,
                    "detail": "grading failed",
                },
                status=500,
            )

        submission.refresh_from_db(fields=["status", "enrollment_id"])
        synced_score = None
        synced_max_score = None
        if submission.target_type == Submission.TargetType.EXAM and submission.enrollment_id:
            try:
                from apps.domains.results.models import Result
                synced_result = (
                    Result.objects
                    .filter(
                        target_type="exam",
                        target_id=int(submission.target_id),
                        enrollment_id=int(submission.enrollment_id),
                        enrollment__tenant=tenant,
                    )
                    .only("total_score", "max_score")
                    .order_by("-id")
                    .first()
                )
                if synced_result:
                    synced_score = float(synced_result.total_score or 0.0)
                    synced_max_score = float(synced_result.max_score or 0.0)
            except Exception:
                synced_score = None
                synced_max_score = None

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
                "updated": updated,
                "graded": True,
                "result_id": getattr(result_obj, "id", None),
                "resolved_enrollment_id": resolved_enrollment_id,
                "enrollment_id": submission.enrollment_id,
                "score": synced_score,
                "total_score": synced_score,
                "max_score": synced_max_score,
            }
        )

    # 폐기 사유 enum — 운영자 audit 용 세분화. 외 값은 "other" 로 fold.
    _DISCARD_REASONS = {
        "scan_quality",       # 스캔/사진 품질 불량
        "wrong_upload",       # 오업로드
        "duplicate",          # 중복 업로드
        "target_missing",     # 원본 시험/과제 없음 (orphan)
        "operator_discarded", # 단순 운영자 폐기 (default)
        "other",
    }

    @action(detail=False, methods=["post"], url_path="discard-batch")
    def discard_batch(self, request):
        """
        여러 답안지를 일괄 폐기.
        body: {"submission_ids": [int, ...], "reason": "operator_discarded" | ...}
        - 본인 tenant 의 submission 만 처리. 그 외는 silent skip + skipped 카운트로 보고.
        - 이미 DONE/SUPERSEDED 는 transition 차단되어 skipped 처리.
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)

        ids = request.data.get("submission_ids") or []
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "submission_ids 필수"}, status=400)
        # 운영자 misuse 방지 — 1회 호출당 최대 500건. 그 이상은 분할 요청 강제.
        if len(ids) > 500:
            return Response(
                {"detail": "한 번에 폐기 가능한 최대 건수는 500건입니다.", "code": "BATCH_TOO_LARGE"},
                status=400,
            )
        try:
            ids = [int(x) for x in ids]
        except (TypeError, ValueError):
            return Response({"detail": "submission_ids 는 정수 배열"}, status=400)

        raw_reason = str(request.data.get("reason") or "operator_discarded").strip()
        reason = raw_reason if raw_reason in self._DISCARD_REASONS else "other"

        discarded = 0
        skipped: list[dict] = []
        now = timezone.now()
        with transaction.atomic():
            qs = Submission.objects.select_for_update().filter(tenant=tenant, id__in=ids)
            for s in qs:
                try:
                    transit_save(
                        s, Submission.Status.FAILED,
                        admin_override=True,
                        error_message=f"discarded:{reason}",
                        actor=f"admin.discard_batch.user_{getattr(request.user, 'id', '?')}",
                    )
                except InvalidTransitionError as e:
                    skipped.append({"id": s.id, "reason": str(e)})
                    continue

                meta = dict(s.meta or {})
                meta["discarded"] = {
                    "at": now.isoformat(),
                    "by_user_id": getattr(request.user, "id", None),
                    "reason": reason,
                    "batch": True,
                }
                meta.setdefault("manual_review", {})
                meta["manual_review"]["required"] = False
                meta["manual_review"]["resolved_at"] = now.isoformat()
                s.meta = meta
                s.save(update_fields=["meta", "updated_at"])
                discarded += 1

        return Response({
            "discarded": discarded,
            "skipped_count": len(skipped),
            "skipped": skipped[:20],
            "reason": reason,
        }, status=200)

    @action(detail=True, methods=["post"], url_path="discard")
    def discard(self, request, pk=None):
        """
        OMR 답안지 폐기 — 스캔 품질 불량/오업로드/중복 등으로 채점 대상에서 제외.
        - 상태 FAILED로 전환하고 meta.discarded 기록 (감사 목적).
        - 본 제출에 매칭된 enrollment_id는 유지(기록용)하되 채점 미시행.
        body (optional): {"reason": "scan_quality" | "wrong_upload" | "duplicate" | "target_missing" | "other"}
        """
        raw_reason = str(request.data.get("reason") or "operator_discarded").strip()
        reason = raw_reason if raw_reason in self._DISCARD_REASONS else "other"

        with transaction.atomic():
            submission: Submission = self.get_queryset().select_for_update().get(pk=pk)
            try:
                transit_save(
                    submission, Submission.Status.FAILED,
                    admin_override=True,
                    error_message=f"discarded:{reason}",
                    actor=f"admin.discard.user_{getattr(request.user, 'id', '?')}",
                )
            except InvalidTransitionError as e:
                return Response({"detail": str(e)}, status=409)

            now = timezone.now()
            meta = dict(submission.meta or {})
            meta["discarded"] = {
                "at": now.isoformat(),
                "by_user_id": getattr(request.user, "id", None),
                "reason": reason,
            }
            meta.setdefault("manual_review", {})
            meta["manual_review"]["required"] = False
            meta["manual_review"]["resolved_at"] = now.isoformat()
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
