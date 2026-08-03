from __future__ import annotations

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
        requested: dict[int, tuple[int, bool]] = {}
        for raw in raw_items:
            try:
                proposal_id = int(raw.get("id"))
                number = int(raw.get("number"))
                included = raw.get("included", True)
            except (AttributeError, TypeError, ValueError):
                raise ValidationError({"items": "문항 번호를 다시 확인해 주세요."})
            if not isinstance(included, bool):
                raise ValidationError({"items": "포함 여부를 다시 확인해 주세요."})
            if proposal_id <= 0 or number <= 0 or number > 999:
                raise ValidationError({"items": "문항 번호는 1~999만 가능합니다."})
            if proposal_id in requested:
                raise ValidationError({"items": "같은 후보가 중복되었습니다."})
            requested[proposal_id] = (number, included)

        with transaction.atomic():
            exam = Exam.objects.select_for_update().filter(
                id=int(exam_id),
                tenant=request.tenant,
                exam_type=Exam.ExamType.REGULAR,
            ).first()
            if not exam:
                return Response({"detail": "시험을 찾을 수 없습니다."}, status=404)
            if exam.segmentation_status != Exam.SegmentationStatus.REVIEW_REQUIRED:
                return Response(
                    {"detail": "검수 대기 중인 시험만 확정할 수 있습니다."},
                    status=status.HTTP_409_CONFLICT,
                )
            if ExamQuestion.objects.filter(sheet__exam=exam).exists():
                return Response(
                    {"detail": "이미 확정된 문항이 있어 덮어쓰지 않았습니다."},
                    status=status.HTTP_409_CONFLICT,
                )
            proposals = list(
                ExamQuestionProposal.objects.select_for_update()
                .filter(exam=exam)
                .order_by("position", "id")
            )
            if set(requested) != {int(item.id) for item in proposals}:
                raise ValidationError({"items": "검수 후보가 변경되었습니다. 새로고침해 주세요."})
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
                    image_key=proposal.problem_image_key,
                    region_meta={
                        **(proposal.region_meta or {}),
                        "page_index": proposal.page_index,
                        "detected_number": proposal.detected_number,
                    },
                    question_kind=(
                        ExamQuestion.QuestionKind.CHOICE
                        if index <= choice_count
                        else ExamQuestion.QuestionKind.ESSAY
                    ),
                    score=(
                        round(float(exam.max_score or 0.0) - base_score * (total - 1), 2)
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

        try:
            from apps.domains.ai.gateway import dispatch_job

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
