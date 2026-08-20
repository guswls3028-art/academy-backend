# PATH: apps/domains/homework_results/views/homework_view.py
"""
Homework API (List/Retrieve/Create)

✅ 프론트 요구사항
- GET /homeworks/?session_id={sessionId}
- GET /homeworks/{id}/
- POST /homeworks/ (session_id, title; optional template_homework_id로 템플릿 불러오기)
"""

from __future__ import annotations

from importlib import import_module
from math import isfinite

from rest_framework import status
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import OrderingFilter
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.decorators import action

from django.db import transaction
from django.db.models import Max, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.core.permissions import TenantResolvedAndMember
from apps.core.optimistic_concurrency import assert_expected_updated_at
from apps.api.common.query_params import parse_query_bool, parse_query_int

from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.homework_results.serializers.homework import HomeworkSerializer
from apps.domains.homework_results.services.max_score_sync import (
    sync_homework_primary_score_max,
    validate_homework_max_score,
)
from apps.domains.homework_results.services.policy_recalc import (
    recalc_scores_for_homework_change,
)
from apps.support.homework_results.homework_view_dependencies import (
    create_workbook_source_exam,
    delete_homework_assignments,
    get_homework_raw_score_cutline,
    get_session_for_homework,
    get_teacher_or_admin_permission,
    homework_assignment_enrollment_ids,
    homework_assignments_for_question_grading,
    workbook_source_is_ready,
    workbook_source_none_status,
)


class HomeworkViewSet(ModelViewSet):
    permission_classes = [
        IsAuthenticated,
        TenantResolvedAndMember,
        get_teacher_or_admin_permission(),
    ]
    serializer_class = HomeworkSerializer

    filter_backends = [OrderingFilter]
    ordering_fields = ["id", "created_at", "updated_at", "display_order"]
    ordering = ["display_order", "created_at", "id"]

    def _next_display_order(self, *, tenant, session_id: int) -> int:
        max_order = (
            Homework.objects
            .filter(tenant=tenant, session_id=int(session_id))
            .exclude(meta__removed_from_session_at__isnull=False)
            .aggregate(value=Max("display_order"))
            .get("value")
        )
        return int(max_order or 0) + 1

    def _resolve_removed_homework_clinic_links(self, *, request, homework: Homework) -> int:
        if homework.session_id is None:
            return 0
        resolve_removed_source_clinic_links = import_module(
            "apps.domains.progress.dispatcher"
        ).resolve_removed_source_clinic_links

        return resolve_removed_source_clinic_links(
            tenant_id=int(request.tenant.id),
            session_id=int(homework.session_id),
            source_type="homework",
            source_id=int(homework.id),
            user_id=getattr(request.user, "id", None),
            reason="homework_removed_from_session",
        )

    def get_queryset(self) -> QuerySet[Homework]:
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return Homework.objects.none()
        qs = Homework.objects.filter(
            tenant=tenant
        ).select_related(
            "session",
            "session__lecture",
            "session__lecture__tenant",
            "session__homework_policy",
            "template_homework",
            "source_exam",
            "source_exam__sheet",
        )

        session_id = parse_query_int(
            self.request.query_params, "session_id", min_value=1
        )
        if session_id is not None:
            qs = qs.filter(session_id=session_id)

        homework_type = self.request.query_params.get("homework_type")
        if homework_type:
            qs = qs.filter(homework_type=str(homework_type).lower())

        if session_id:
            qs = qs.filter(homework_type=Homework.HomeworkType.REGULAR)

        include_removed = parse_query_bool(
            self.request.query_params, "include_removed", default=False
        )
        if not include_removed:
            qs = qs.exclude(meta__removed_from_session_at__isnull=False)

        return qs

    @staticmethod
    def _question_grading_payload(*, homework: Homework) -> dict:
        source_exam = homework.source_exam
        questions = []
        if source_exam is not None:
            try:
                questions = list(
                    source_exam.sheet.questions.order_by("number", "id").values(
                        "id", "number", "image_key"
                    )
                )
            except Exception:
                questions = []

        assignments = homework_assignments_for_question_grading(
            homework=homework,
        )
        enrollment_ids = [int(item.enrollment_id) for item in assignments]
        scores = {
            int(item.enrollment_id): item
            for item in HomeworkScore.objects.filter(
                homework=homework,
                session_id=homework.session_id,
                enrollment_id__in=enrollment_ids,
                attempt_index=1,
            )
        }
        rows = []
        for assignment in assignments:
            score = scores.get(int(assignment.enrollment_id))
            meta = dict(getattr(score, "meta", None) or {})
            marks = meta.get("question_marks")
            rows.append(
                {
                    "enrollment_id": int(assignment.enrollment_id),
                    "student_id": int(assignment.enrollment.student_id),
                    "student_name": str(assignment.enrollment.student.name or ""),
                    "score_id": int(score.id) if score else None,
                    "marks": marks if isinstance(marks, dict) else {},
                }
            )
        return {
            "homework_id": int(homework.id),
            "source_exam_id": int(source_exam.id) if source_exam else None,
            "source_status": (
                str(source_exam.segmentation_status)
                if source_exam
                else workbook_source_none_status()
            ),
            "questions": questions,
            "rows": rows,
        }

    @action(detail=True, methods=["post"], url_path="source-exam")
    def ensure_source_exam(self, request, pk=None):
        with transaction.atomic():
            homework = get_object_or_404(
                self.get_queryset().select_for_update(of=("self",)),
                pk=pk,
            )
            if homework.homework_type != Homework.HomeworkType.REGULAR:
                raise ValidationError(
                    {"detail": "운영 과제에만 워크북 원본을 등록할 수 있습니다."}
                )
            if homework.source_exam_id is None:
                source_exam = create_workbook_source_exam(
                    tenant=request.tenant,
                    homework=homework,
                )
                homework.source_exam = source_exam
                homework.save(update_fields=["source_exam", "updated_at"])
            return Response(HomeworkSerializer(homework).data)

    @action(
        detail=True,
        methods=["get", "patch"],
        url_path="question-grading",
    )
    def question_grading(self, request, pk=None):
        homework = get_object_or_404(self.get_queryset(), pk=pk)
        source_exam = homework.source_exam
        if not workbook_source_is_ready(source_exam):
            return Response(
                {"detail": "워크북 문항을 먼저 분리하고 검수를 확정해 주세요."},
                status=status.HTTP_409_CONFLICT,
            )
        if request.method == "GET":
            return Response(self._question_grading_payload(homework=homework))

        updates = request.data.get("updates")
        if not isinstance(updates, list) or not updates:
            raise ValidationError({"updates": "변경할 학생 문항 목록이 필요합니다."})
        if len(updates) > 500:
            raise ValidationError({"updates": "한 번에 500개 문항까지만 저장할 수 있습니다."})

        valid_numbers = set(
            source_exam.sheet.questions.values_list("number", flat=True)
        )
        assignment_enrollment_ids = homework_assignment_enrollment_ids(
            homework=homework,
        )
        parsed = []
        seen = set()
        for raw in updates:
            try:
                enrollment_id = int(raw.get("enrollment_id"))
                question_number = int(raw.get("question_number"))
            except (AttributeError, TypeError, ValueError):
                raise ValidationError({"updates": "학생과 문항 번호를 다시 확인해 주세요."})
            is_correct = raw.get("is_correct")
            include = raw.get("include_in_wrong_note", False)
            if is_correct not in (True, False, None) or not isinstance(include, bool):
                raise ValidationError({"updates": "정오 및 복습 표시 값을 다시 확인해 주세요."})
            if is_correct is None and include:
                raise ValidationError({"updates": "복습 문항은 O 또는 X를 먼저 선택해 주세요."})
            key = (enrollment_id, question_number)
            if key in seen:
                raise ValidationError({"updates": "같은 학생 문항이 중복되었습니다."})
            seen.add(key)
            if enrollment_id not in assignment_enrollment_ids:
                raise ValidationError({"updates": "이 과제의 대상 학생만 채점할 수 있습니다."})
            if question_number not in valid_numbers:
                raise ValidationError({"updates": "현재 워크북에 없는 문항입니다."})
            parsed.append((enrollment_id, question_number, is_correct, include))

        with transaction.atomic():
            for enrollment_id, question_number, is_correct, include in parsed:
                score, _ = HomeworkScore.objects.select_for_update().get_or_create(
                    enrollment_id=enrollment_id,
                    session_id=homework.session_id,
                    homework=homework,
                    attempt_index=1,
                    defaults={"max_score": homework.default_max_score},
                )
                meta = dict(score.meta or {})
                raw_marks = meta.get("question_marks")
                marks = dict(raw_marks) if isinstance(raw_marks, dict) else {}
                mark_key = str(question_number)
                if is_correct is None and not include:
                    marks.pop(mark_key, None)
                else:
                    marks[mark_key] = {
                        "is_correct": is_correct,
                        "include_in_wrong_note": include,
                        "updated_by_user_id": int(request.user.id),
                        "updated_at": timezone.now().isoformat(),
                    }
                meta["question_marks"] = marks
                score.meta = meta
                score.updated_by_user = request.user
                score.save(update_fields=["meta", "updated_by_user", "updated_at"])

        homework = self.get_queryset().get(pk=homework.pk)
        return Response(self._question_grading_payload(homework=homework))

    def create(self, request, *args, **kwargs):
        """템플릿 불러오기 시 serializer 검증 없이 생성."""
        data = request.data
        template_id = data.get("template_homework_id") or data.get("template_homework")
        session_id = data.get("session_id") or data.get("session")
        if not session_id:
            return Response(
                {"session_id": "필수입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant is required."}, status=status.HTTP_403_FORBIDDEN)
        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            return Response(
                {"session_id": "정수여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session = get_session_for_homework(session_id=session_id, tenant=tenant)
        if session is None:
            return Response(
                {"detail": "해당 차시를 찾을 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if template_id:
            try:
                template = Homework.objects.get(
                    id=int(template_id),
                    tenant=tenant,
                    homework_type=Homework.HomeworkType.TEMPLATE,
                )
            except (ValueError, TypeError, Homework.DoesNotExist):
                return Response(
                    {"template_homework_id": "유효한 과제 템플릿이 아닙니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            with transaction.atomic():
                session = get_session_for_homework(
                    session_id=int(session.id),
                    tenant=tenant,
                    for_update=True,
                )
                if session is None:
                    return Response(
                        {"detail": "해당 차시를 찾을 수 없습니다."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                requested_max_score = data.get("max_score")
                if requested_max_score is None:
                    candidate_max_score = template.default_max_score
                else:
                    try:
                        candidate_max_score = float(requested_max_score)
                    except (TypeError, ValueError, OverflowError):
                        raise ValidationError({"max_score": "만점은 1 이상의 숫자여야 합니다."})
                    if not isfinite(candidate_max_score) or candidate_max_score < 1:
                        raise ValidationError({"max_score": "만점은 1 이상이어야 합니다."})
                raw_cutline = get_homework_raw_score_cutline(session=session)
                if (
                    template.cutline_mode == Homework.CutlineMode.COUNT
                    and template.cutline_value is not None
                ):
                    raw_cutline = float(template.cutline_value)
                elif (
                    template.cutline_mode == Homework.CutlineMode.PERCENT
                    and template.cutline_value is not None
                ):
                    raw_cutline = None
                if raw_cutline is not None and raw_cutline > candidate_max_score:
                    raise ValidationError(
                        {
                            "max_score": (
                                f"점수 커트라인({raw_cutline:g}점)보다 만점"
                                f"({candidate_max_score:g}점)을 낮게 설정할 수 없습니다."
                            )
                        }
                    )
                title = (data.get("title") or "").strip() or template.title
                instance = Homework.objects.create(
                    tenant=tenant,
                    homework_type=Homework.HomeworkType.REGULAR,
                    session=session,
                    template_homework=template,
                    title=title,
                    grading_mode=template.grading_mode,
                    meta={"default_max_score": candidate_max_score},
                    cutline_mode=template.cutline_mode,
                    cutline_value=template.cutline_value,
                    round_unit_percent=template.round_unit_percent,
                    display_order=self._next_display_order(
                        tenant=tenant,
                        session_id=int(session.id),
                    ),
                )
            return Response(
                HomeworkSerializer(instance).data,
                status=status.HTTP_201_CREATED,
            )
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        data = self.request.data
        session_id = data.get("session_id") or data.get("session")
        if not session_id:
            raise ValidationError({"session_id": "필수입니다."})
        tenant = getattr(self.request, "tenant", None)
        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            raise ValidationError({"session_id": "정수여야 합니다."})
        with transaction.atomic():
            session = get_session_for_homework(
                session_id=session_id,
                tenant=tenant,
                for_update=True,
            )
            if session is None:
                raise ValidationError({"detail": "해당 차시를 찾을 수 없습니다."})
            candidate_meta = serializer.validated_data.get("meta")
            candidate_max_score = Homework.max_score_from_meta(candidate_meta)
            candidate_cutline_mode = serializer.validated_data.get("cutline_mode")
            candidate_cutline_value = serializer.validated_data.get("cutline_value")
            if candidate_cutline_mode == Homework.CutlineMode.COUNT:
                raw_cutline = float(candidate_cutline_value)
            elif candidate_cutline_mode == Homework.CutlineMode.PERCENT:
                raw_cutline = None
            else:
                raw_cutline = get_homework_raw_score_cutline(session=session)
            if raw_cutline is not None and raw_cutline > candidate_max_score:
                raise ValidationError(
                    {
                        "max_score": (
                            f"점수 커트라인({raw_cutline:g}점)보다 만점"
                            f"({candidate_max_score:g}점)을 낮게 설정할 수 없습니다."
                        )
                    }
                )
            serializer.save(
                tenant=tenant,
                homework_type=Homework.HomeworkType.REGULAR,
                session=session,
                title=(data.get("title") or "").strip() or "제목 없음",
                display_order=self._next_display_order(
                    tenant=tenant,
                    session_id=int(session.id),
                ),
            )

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        instance = get_object_or_404(
            self.filter_queryset(
                self.get_queryset().select_for_update(of=("self",))
            ),
            pk=kwargs["pk"],
        )
        self.check_object_permissions(request, instance)
        assert_expected_updated_at(request=request, instance=instance)
        old_max_score = instance.default_max_score
        old_cutline = (
            instance.cutline_mode,
            instance.cutline_value,
            instance.round_unit_percent,
        )
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        next_grading_mode = serializer.validated_data.get(
            "grading_mode",
            instance.grading_mode,
        )
        grading_mode_changed = next_grading_mode != instance.grading_mode
        if grading_mode_changed and instance.scores.exists():
            raise ValidationError(
                {
                    "grading_mode": (
                        "이미 결과가 입력된 과제는 채점 방식을 바꿀 수 없습니다. "
                        "기존 결과를 보존하려면 새 과제를 만들어 주세요."
                    )
                }
            )
        candidate_meta = serializer.validated_data.get("meta", instance.meta)
        candidate_max_score = Homework.max_score_from_meta(candidate_meta)

        explicit_max_score = "max_score" in request.data or (
            isinstance(request.data.get("meta"), dict)
            and "default_max_score" in request.data["meta"]
        )
        should_sync_scores = (
            candidate_max_score != old_max_score
            or explicit_max_score
            or grading_mode_changed
        )

        if should_sync_scores:
            candidate_cutline_mode = serializer.validated_data.get(
                "cutline_mode",
                instance.cutline_mode,
            )
            candidate_cutline_value = serializer.validated_data.get(
                "cutline_value",
                instance.cutline_value,
            )
            if candidate_cutline_mode == Homework.CutlineMode.COUNT:
                candidate_raw_cutline = float(candidate_cutline_value)
            elif candidate_cutline_mode == Homework.CutlineMode.PERCENT:
                candidate_raw_cutline = None
            else:
                candidate_raw_cutline = get_homework_raw_score_cutline(
                    session=instance.session,
                )
            try:
                validate_homework_max_score(
                    homework=instance,
                    max_score=candidate_max_score,
                    raw_score_cutline=candidate_raw_cutline,
                )
            except ValueError as exc:
                raise ValidationError({"max_score": str(exc)}) from exc

        self.perform_update(serializer)
        if should_sync_scores:
            sync_homework_primary_score_max(
                homework=serializer.instance,
                max_score=candidate_max_score,
            )
        else:
            new_cutline = (
                serializer.instance.cutline_mode,
                serializer.instance.cutline_value,
                serializer.instance.round_unit_percent,
            )
            explicit_cutline = any(
                field in request.data
                for field in ("cutline_mode", "cutline_value", "round_unit_percent")
            )
            if explicit_cutline or new_cutline != old_cutline:
                recalc_scores_for_homework_change(homework=serializer.instance)

        return Response(self.get_serializer(serializer.instance).data)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        """Remove a homework from the live session without deleting score/submission history."""
        tenant = getattr(request, "tenant", None)
        homework = get_object_or_404(
            Homework.objects.select_for_update()
            .filter(tenant=tenant)
            .exclude(meta__removed_from_session_at__isnull=False),
            pk=kwargs["pk"],
        )

        removed_clinic_link_count = self._resolve_removed_homework_clinic_links(
            request=request,
            homework=homework,
        )
        assignment_count = delete_homework_assignments(
            tenant=tenant,
            homework=homework,
        )

        meta = dict(homework.meta or {})
        meta["removed_from_session_at"] = timezone.now().isoformat()
        meta["removed_from_session_by_user_id"] = getattr(request.user, "id", None)
        meta["removed_assignment_count"] = int(assignment_count)
        meta["removed_clinic_link_count"] = int(removed_clinic_link_count)
        homework.meta = meta
        homework.save(update_fields=["meta", "updated_at"])

        return Response(status=status.HTTP_204_NO_CONTENT)
