from __future__ import annotations

from io import BytesIO
import logging
import uuid

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.exams.models import (
    Exam,
    ExamQuestion,
    ExamQuestionProposal,
    QuestionExplanation,
    Sheet,
)
from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
from apps.infrastructure.storage.r2 import (
    delete_object_r2_storage,
    get_object_bytes_r2_storage,
    upload_fileobj_to_r2_storage,
)
from academy.adapters.tools.hwp_endnote_images import crop_problem_from_endnote


logger = logging.getLogger(__name__)
_MIN_PROBLEM_CROP_RATIO = 0.08
_MAX_PROBLEM_CROP_RATIO = 0.98
_MAX_EXPLANATION_IMAGE_BYTES = 10 * 1024 * 1024


def _proposal_url(*, tenant_id: int, key: str) -> str:
    expected = f"tenants/{tenant_id}/exams/"
    if not key or not key.startswith(expected):
        return ""
    return generate_presigned_get_url_storage(key=key, expires_in=3600)


class ExamSegmentationReviewView(APIView):
    permission_classes = [TenantResolvedAndStaff]

    def get(self, request, exam_id: int):
        exam = Exam.objects.filter(
            id=int(exam_id),
            tenant=request.tenant,
            exam_type=Exam.ExamType.REGULAR,
        ).first()
        if not exam:
            return Response({"detail": "시험을 찾을 수 없습니다."}, status=404)
        proposals = list(exam.question_proposals.all())
        return Response(
            {
                "exam_id": int(exam.id),
                "status": exam.segmentation_status,
                "source_filename": exam.source_filename,
                "items": [
                    {
                        "id": int(item.id),
                        "position": item.position,
                        "number": item.number,
                        "detected_number": item.detected_number,
                        "page_index": item.page_index,
                        "included": item.included,
                        "engine": item.engine,
                        "problem_crop_ratio": item.problem_crop_ratio,
                        "crop_adjustable": bool(
                            item.engine == "hwp_endnote"
                            and item.explanation_image_key
                        ),
                        "problem_image_url": _proposal_url(
                            tenant_id=int(request.tenant.id),
                            key=item.problem_image_key,
                        ),
                        "explanation_text": item.explanation_text,
                        "explanation_image_url": _proposal_url(
                            tenant_id=int(request.tenant.id),
                            key=item.explanation_image_key,
                        ),
                        "has_teacher_explanation": bool(
                            item.explanation_text or item.explanation_image_key
                        ),
                    }
                    for item in proposals
                ],
            }
        )


class ExamSegmentationApproveView(APIView):
    permission_classes = [TenantResolvedAndStaff]

    def post(self, request, exam_id: int):
        raw_items = request.data.get("items")
        if not isinstance(raw_items, list):
            raise ValidationError({"items": "검수한 문항 목록이 필요합니다."})

        requested: dict[int, tuple[int, bool, float | None]] = {}
        for raw in raw_items:
            try:
                proposal_id = int(raw.get("id"))
                number = int(raw.get("number"))
                included = raw.get("included", True)
                raw_ratio = raw.get("problem_crop_ratio")
                crop_ratio = float(raw_ratio) if raw_ratio is not None else None
            except (AttributeError, TypeError, ValueError):
                raise ValidationError({"items": "문항 번호를 다시 확인해 주세요."})
            if not isinstance(included, bool):
                raise ValidationError({"items": "포함 여부를 다시 확인해 주세요."})
            if proposal_id <= 0 or number <= 0 or number > 999:
                raise ValidationError({"items": "문항 번호는 1~999만 가능합니다."})
            if crop_ratio is not None and not (
                _MIN_PROBLEM_CROP_RATIO <= crop_ratio <= _MAX_PROBLEM_CROP_RATIO
            ):
                raise ValidationError(
                    {"items": "문제 영역은 원본 높이의 8~98% 사이여야 합니다."}
                )
            if proposal_id in requested:
                raise ValidationError({"items": "같은 후보가 중복되었습니다."})
            requested[proposal_id] = (number, included, crop_ratio)

        exam_snapshot = Exam.objects.filter(
            id=int(exam_id),
            tenant=request.tenant,
            exam_type=Exam.ExamType.REGULAR,
        ).first()
        if not exam_snapshot:
            return Response({"detail": "시험을 찾을 수 없습니다."}, status=404)
        if exam_snapshot.segmentation_status != Exam.SegmentationStatus.REVIEW_REQUIRED:
            return Response(
                {"detail": "검수 대기 중인 시험만 확정할 수 있습니다."},
                status=status.HTTP_409_CONFLICT,
            )

        proposal_snapshot = list(
            ExamQuestionProposal.objects.filter(exam=exam_snapshot).order_by(
                "position", "id"
            )
        )
        if set(requested) != {int(item.id) for item in proposal_snapshot}:
            raise ValidationError({"items": "검수 후보가 변경되었습니다. 새로고침해 주세요."})

        staged_problem_keys: dict[int, str] = {}
        staged_ratios: dict[int, float] = {}
        old_problem_keys: set[str] = set()
        expected_prefix = f"tenants/{int(request.tenant.id)}/exams/"

        try:
            for proposal in proposal_snapshot:
                _, included, requested_ratio = requested[int(proposal.id)]
                if not included or requested_ratio is None:
                    continue
                if abs(requested_ratio - float(proposal.problem_crop_ratio)) < 0.0001:
                    continue
                if (
                    proposal.engine != "hwp_endnote"
                    or not proposal.explanation_image_key.startswith(expected_prefix)
                    or not proposal.problem_image_key.startswith(expected_prefix)
                ):
                    raise ValidationError(
                        {"items": "이 문항은 원본 문제 영역을 조절할 수 없습니다."}
                    )
                source = get_object_bytes_r2_storage(
                    key=proposal.explanation_image_key,
                    max_bytes=_MAX_EXPLANATION_IMAGE_BYTES,
                )
                if not source:
                    raise ValidationError(
                        {"items": f"{proposal.number}번 원본 해설 이미지를 불러오지 못했습니다."}
                    )
                new_key = (
                    f"tenants/{int(request.tenant.id)}/exams/questions/"
                    f"{int(exam_snapshot.id)}/q{proposal.number:03d}-"
                    f"p{int(proposal.id)}-r{round(requested_ratio * 10000):04d}-"
                    f"{uuid.uuid4().hex[:8]}.png"
                )
                upload_fileobj_to_r2_storage(
                    fileobj=BytesIO(
                        crop_problem_from_endnote(source, requested_ratio)
                    ),
                    key=new_key,
                    content_type="image/png",
                )
                staged_problem_keys[int(proposal.id)] = new_key
                staged_ratios[int(proposal.id)] = requested_ratio
                old_problem_keys.add(proposal.problem_image_key)

            with transaction.atomic():
                exam = Exam.objects.select_for_update().filter(
                    id=int(exam_id),
                    tenant=request.tenant,
                    exam_type=Exam.ExamType.REGULAR,
                ).first()
                if not exam:
                    raise ValidationError({"items": "시험을 찾을 수 없습니다."})
                if exam.segmentation_status != Exam.SegmentationStatus.REVIEW_REQUIRED:
                    raise ValidationError({"items": "검수 상태가 변경되었습니다."})
                if ExamQuestion.objects.filter(sheet__exam=exam).exists():
                    raise ValidationError(
                        {"items": "이미 확정된 문항이 있어 덮어쓰지 않았습니다."}
                    )

                proposals = list(
                    ExamQuestionProposal.objects.select_for_update()
                    .filter(exam=exam)
                    .order_by("position", "id")
                )
                if set(requested) != {int(item.id) for item in proposals}:
                    raise ValidationError(
                        {"items": "검수 후보가 변경되었습니다. 새로고침해 주세요."}
                    )
                selected = [
                    (item, requested[int(item.id)][0])
                    for item in proposals
                    if requested[int(item.id)][1]
                ]
                numbers = [number for _, number in selected]
                if not selected:
                    raise ValidationError({"items": "한 문항 이상 포함해 주세요."})
                if len(numbers) != len(set(numbers)):
                    raise ValidationError({"items": "문항 번호가 중복되었습니다."})

                total = len(selected)
                if exam.grading_mode == Exam.GradingMode.CHOICE:
                    choice_count = total
                elif exam.grading_mode == Exam.GradingMode.WRITTEN:
                    choice_count = 0
                else:
                    choice_count = (
                        min(max(int(exam.choice_question_count or 1), 1), total - 1)
                        if total > 1
                        else 0
                    )

                sheet, _ = Sheet.objects.get_or_create(
                    exam=exam,
                    defaults={"name": "MAIN"},
                )
                sheet.name = "MAIN"
                sheet.total_questions = total
                sheet.choice_count = choice_count
                sheet.essay_count = max(total - choice_count, 0)
                sheet.save(
                    update_fields=[
                        "name",
                        "total_questions",
                        "choice_count",
                        "essay_count",
                        "updated_at",
                    ]
                )

                base_score = round(float(exam.max_score or 0.0) / total, 2)
                for index, (proposal, number) in enumerate(selected, start=1):
                    question = ExamQuestion.objects.create(
                        sheet=sheet,
                        number=number,
                        image_key=staged_problem_keys.get(
                            int(proposal.id), proposal.problem_image_key
                        ),
                        region_meta={
                            **(proposal.region_meta or {}),
                            "page_index": proposal.page_index,
                            "detected_number": proposal.detected_number,
                            "problem_crop_ratio": staged_ratios.get(
                                int(proposal.id), proposal.problem_crop_ratio
                            ),
                        },
                        question_kind=(
                            ExamQuestion.QuestionKind.CHOICE
                            if index <= choice_count
                            else ExamQuestion.QuestionKind.ESSAY
                        ),
                        score=(
                            round(
                                float(exam.max_score or 0.0)
                                - base_score * (total - 1),
                                2,
                            )
                            if index == total
                            else base_score
                        ),
                    )
                    if proposal.explanation_text or proposal.explanation_image_key:
                        QuestionExplanation.objects.create(
                            question=question,
                            text=proposal.explanation_text,
                            image_key=proposal.explanation_image_key,
                            source=QuestionExplanation.Source.SOURCE_FILE,
                            match_confidence=proposal.match_confidence,
                        )

                exam.choice_question_count = choice_count
                exam.segmentation_status = Exam.SegmentationStatus.READY
                exam.save(
                    update_fields=[
                        "choice_question_count",
                        "segmentation_status",
                        "updated_at",
                    ]
                )
                ExamQuestionProposal.objects.filter(exam=exam).delete()
        except Exception:
            for key in staged_problem_keys.values():
                try:
                    delete_object_r2_storage(key=key)
                except Exception:
                    logger.warning(
                        "segmentation recrop cleanup failed",
                        extra={"key": key},
                        exc_info=True,
                    )
            raise

        for key in old_problem_keys:
            try:
                delete_object_r2_storage(key=key)
            except Exception:
                logger.warning(
                    "segmentation old crop cleanup failed",
                    extra={"key": key},
                    exc_info=True,
                )

        try:
            from apps.domains.ai.contracts import dispatch_job

            dispatch_job(
                job_type="matchup_index_exam",
                payload={"exam_id": str(exam.id), "tenant_id": str(exam.tenant_id)},
                tenant_id=str(exam.tenant_id),
                source_domain="matchup_index",
                source_id=str(exam.id),
            )
        except Exception:
            # 문항 확정은 정본이며 유사문항 인덱싱 실패와 분리한다.
            pass
        return Response(
            {
                "exam_id": int(exam.id),
                "status": exam.segmentation_status,
                "total_questions": total,
            }
        )
