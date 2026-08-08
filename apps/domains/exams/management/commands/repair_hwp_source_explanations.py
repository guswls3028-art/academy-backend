"""Repair an exact HWP exam whose approved source images were mis-scoped.

The command is dry-run by default. It preserves every previous object and writes
rollback evidence into each question before switching the canonical explanation.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
import uuid

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from academy.adapters.tools.hwp_endnote_images import extract_document_endnotes
from apps.domains.exams.models import (
    Exam,
    ExamAsset,
    ExamQuestion,
    QuestionExplanation,
)
from apps.infrastructure.storage.r2 import (
    delete_object_r2_storage,
    get_object_bytes_r2_storage,
    upload_fileobj_to_r2_storage,
)


_MAX_HWP_BYTES = 50 * 1024 * 1024
_MAX_RENDER_BYTES = 10 * 1024 * 1024


def _source_asset(exam: Exam) -> ExamAsset:
    asset = exam.assets.filter(asset_type=ExamAsset.AssetType.PROBLEM_SOURCE).first()
    if asset is None:
        raise CommandError(f"exam {exam.pk} has no problem_source asset")
    expected_prefix = f"tenants/{exam.tenant_id}/exams/pdf-extract/"
    if not asset.file_key.startswith(expected_prefix):
        raise CommandError(f"exam {exam.pk} source asset is outside its tenant prefix")
    if Path(exam.source_filename or asset.file_key).suffix.lower() != ".hwp":
        raise CommandError(f"exam {exam.pk} source asset is not an HWP file")
    return asset


def _questions(exam: Exam) -> list[ExamQuestion]:
    try:
        sheet = exam.sheet
    except ObjectDoesNotExist as exc:
        raise CommandError(f"exam {exam.pk} has no canonical sheet") from exc
    questions = list(
        ExamQuestion.objects.filter(sheet=sheet)
        .select_related("explanation")
        .order_by("number")
    )
    if not questions:
        raise CommandError(f"exam {exam.pk} has no approved questions")
    for question in questions:
        try:
            explanation = question.explanation
        except QuestionExplanation.DoesNotExist as exc:
            raise CommandError(f"question {question.number} has no explanation") from exc
        if explanation.source != QuestionExplanation.Source.SOURCE_FILE:
            raise CommandError(
                f"question {question.number} explanation is teacher-edited; refusing repair"
            )
    return questions


def _already_repaired(questions: list[ExamQuestion], source_sha256: str) -> bool:
    states = [
        str(
            ((question.region_meta or {}).get("explanation_repair") or {}).get(
                "source_sha256"
            )
            or ""
        )
        == source_sha256
        for question in questions
    ]
    if any(states) and not all(states):
        raise CommandError("exam has a partial explanation repair; manual audit is required")
    return bool(states and all(states))


def repair_hwp_source_explanations(
    *,
    exam: Exam,
    apply: bool,
) -> dict[str, object]:
    if exam.exam_type != Exam.ExamType.REGULAR:
        raise CommandError(f"exam {exam.pk} is not a regular exam")
    if exam.segmentation_status != Exam.SegmentationStatus.READY:
        raise CommandError(f"exam {exam.pk} is not segmentation-ready")

    asset = _source_asset(exam)
    questions = _questions(exam)
    source_bytes = get_object_bytes_r2_storage(
        key=asset.file_key,
        max_bytes=_MAX_HWP_BYTES,
        timeout_seconds=60,
    )
    if source_bytes is None:
        raise CommandError(f"exam {exam.pk} HWP source is missing or exceeds 50 MiB")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if _already_repaired(questions, source_sha256):
        return {
            "exam_id": exam.pk,
            "tenant_id": exam.tenant_id,
            "mode": "already_repaired",
            "question_count": len(questions),
            "source_sha256": source_sha256,
        }

    filename = exam.source_filename or Path(asset.file_key).name
    with NamedTemporaryFile(suffix=".hwp", delete=False) as source_file:
        source_file.write(source_bytes)
        source_file.flush()
        source_path = source_file.name
    try:
        extraction = extract_document_endnotes(
            source_path,
            filename,
            include_paired_reconstruction=True,
        )
    finally:
        Path(source_path).unlink(missing_ok=True)

    visuals = list(extraction.paired_visuals)
    question_numbers = [int(question.number) for question in questions]
    visual_numbers = [int(visual.number) for visual in visuals]
    if question_numbers != visual_numbers:
        raise CommandError(
            "approved question numbers do not exactly match reconstructed HWP notes: "
            f"questions={question_numbers}, notes={visual_numbers}"
        )
    if any(visual.render_mode != "source_content_reconstruction" for visual in visuals):
        raise CommandError("not every HWP note has a safe text/equation reconstruction")

    report: dict[str, object] = {
        "exam_id": exam.pk,
        "tenant_id": exam.tenant_id,
        "mode": "apply" if apply else "dry-run",
        "question_count": len(questions),
        "source_sha256": source_sha256,
        "preserves_previous_objects": True,
    }
    if not apply:
        return report

    repair_id = uuid.uuid4().hex
    staged: dict[int, tuple[str, bytes]] = {}
    uploaded_keys: list[str] = []
    try:
        for visual in visuals:
            key = (
                f"tenants/{exam.tenant_id}/exams/explanations/{exam.pk}/repairs/"
                f"{source_sha256[:16]}-{repair_id}/q{visual.number:03d}.png"
            )
            upload_fileobj_to_r2_storage(
                fileobj=BytesIO(visual.png_bytes),
                key=key,
                content_type="image/png",
                timeout_seconds=30,
            )
            uploaded_keys.append(key)
            readback = get_object_bytes_r2_storage(
                key=key,
                max_bytes=_MAX_RENDER_BYTES,
                timeout_seconds=30,
            )
            if readback is None or hashlib.sha256(readback).hexdigest() != hashlib.sha256(
                visual.png_bytes
            ).hexdigest():
                raise CommandError(f"question {visual.number} replacement failed readback")
            staged[int(visual.number)] = (key, visual.png_bytes)

        repaired_at = timezone.now().isoformat()
        with transaction.atomic():
            locked_exam = Exam.objects.select_for_update().get(
                pk=exam.pk,
                tenant_id=exam.tenant_id,
            )
            locked_questions = _questions(locked_exam)
            if [int(item.number) for item in locked_questions] != question_numbers:
                raise CommandError("approved questions changed during repair")
            if _already_repaired(locked_questions, source_sha256):
                raise CommandError("exam was repaired concurrently")

            for question in locked_questions:
                explanation = question.explanation
                old_key = explanation.image_key
                new_key, _ = staged[int(question.number)]
                meta = dict(question.region_meta or {})
                meta["source_render_mode"] = "source_content_reconstruction"
                meta["source_attachment_image_key"] = old_key
                meta["source_attachment_requires_review"] = bool(old_key)
                meta["explanation_variant"] = "reconstructed"
                meta["explanation_repair"] = {
                    "kind": "hwp_source_scope_repair",
                    "source_sha256": source_sha256,
                    "previous_image_key": old_key,
                    "replacement_image_key": new_key,
                    "repaired_at": repaired_at,
                }
                question.region_meta = meta
                question.save(update_fields=["region_meta", "updated_at"])
                explanation.image_key = new_key
                explanation.match_confidence = 1.0
                explanation.save(
                    update_fields=["image_key", "match_confidence", "updated_at"]
                )
    except Exception:
        for key in uploaded_keys:
            try:
                delete_object_r2_storage(key=key, timeout_seconds=10)
            except Exception:
                pass
        raise

    report["repair_id"] = repair_id
    report["replaced_count"] = len(staged)
    return report


class Command(BaseCommand):
    help = (
        "Safely replace an exact regular exam's mis-scoped HWP source explanations. "
        "Dry-run unless --apply is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", required=True, type=int)
        parser.add_argument("--exam-id", required=True, type=int)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        tenant_id = int(options["tenant_id"])
        exam_id = int(options["exam_id"])
        exam = Exam.objects.filter(pk=exam_id, tenant_id=tenant_id).first()
        if exam is None:
            raise CommandError(f"exam {exam_id} does not exist in tenant {tenant_id}")
        report = repair_hwp_source_explanations(
            exam=exam,
            apply=bool(options["apply"]),
        )
        self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
