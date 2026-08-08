from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.exams.models import (
    Exam,
    ExamAsset,
    ExamQuestion,
    QuestionExplanation,
    Sheet,
)


class RepairHwpSourceExplanationsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="HWP Repair",
            code="hwp-repair",
            is_active=True,
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="HWP source exam",
            exam_type=Exam.ExamType.REGULAR,
            segmentation_status=Exam.SegmentationStatus.READY,
            source_filename="teacher.hwp",
        )
        ExamAsset.objects.create(
            exam=self.exam,
            asset_type=ExamAsset.AssetType.PROBLEM_SOURCE,
            file_key=(
                f"tenants/{self.tenant.id}/exams/pdf-extract/source/teacher.hwp"
            ),
        )
        sheet = Sheet.objects.create(exam=self.exam, total_questions=2)
        for number in (1, 2):
            question = ExamQuestion.objects.create(
                sheet=sheet,
                number=number,
                region_meta={"existing": True},
            )
            QuestionExplanation.objects.create(
                question=question,
                image_key=f"tenants/{self.tenant.id}/exams/explanations/{self.exam.id}/q{number:03d}.png",
                source=QuestionExplanation.Source.SOURCE_FILE,
            )

    @staticmethod
    def _extraction():
        return SimpleNamespace(
            paired_visuals=tuple(
                SimpleNamespace(
                    number=number,
                    png_bytes=f"safe-{number}".encode(),
                    render_mode="source_content_reconstruction",
                )
                for number in (1, 2)
            )
        )

    @patch(
        "apps.domains.exams.management.commands.repair_hwp_source_explanations.extract_document_endnotes"
    )
    @patch(
        "apps.domains.exams.management.commands.repair_hwp_source_explanations.get_object_bytes_r2_storage"
    )
    def test_dry_run_does_not_change_explanations(self, get_bytes, extract):
        get_bytes.return_value = b"source-hwp"
        extract.return_value = self._extraction()
        before = list(
            QuestionExplanation.objects.order_by("question__number").values_list(
                "image_key", flat=True
            )
        )

        stdout = StringIO()
        call_command(
            "repair_hwp_source_explanations",
            tenant_id=self.tenant.id,
            exam_id=self.exam.id,
            stdout=stdout,
        )

        self.assertIn('"mode": "dry-run"', stdout.getvalue())
        self.assertEqual(
            list(
                QuestionExplanation.objects.order_by("question__number").values_list(
                    "image_key", flat=True
                )
            ),
            before,
        )

    @patch(
        "apps.domains.exams.management.commands.repair_hwp_source_explanations.extract_document_endnotes"
    )
    @patch(
        "apps.domains.exams.management.commands.repair_hwp_source_explanations.delete_object_r2_storage"
    )
    @patch(
        "apps.domains.exams.management.commands.repair_hwp_source_explanations.upload_fileobj_to_r2_storage"
    )
    @patch(
        "apps.domains.exams.management.commands.repair_hwp_source_explanations.get_object_bytes_r2_storage"
    )
    def test_apply_preserves_old_keys_as_review_attachments(
        self,
        get_bytes,
        upload,
        delete,
        extract,
    ):
        source = b"source-hwp"
        safe_by_key: dict[str, bytes] = {}

        def upload_side_effect(*, fileobj, key, **_kwargs):
            safe_by_key[key] = fileobj.read()

        def read_side_effect(*, key, **_kwargs):
            if key.endswith("teacher.hwp"):
                return source
            return safe_by_key.get(key)

        get_bytes.side_effect = read_side_effect
        upload.side_effect = upload_side_effect
        extract.return_value = self._extraction()

        call_command(
            "repair_hwp_source_explanations",
            tenant_id=self.tenant.id,
            exam_id=self.exam.id,
            apply=True,
            stdout=StringIO(),
        )

        questions = list(
            ExamQuestion.objects.select_related("explanation").order_by("number")
        )
        self.assertEqual(upload.call_count, 2)
        delete.assert_not_called()
        for question in questions:
            repair = question.region_meta["explanation_repair"]
            self.assertEqual(
                question.region_meta["source_attachment_image_key"],
                repair["previous_image_key"],
            )
            self.assertEqual(
                question.explanation.image_key,
                repair["replacement_image_key"],
            )
            self.assertEqual(
                question.region_meta["source_render_mode"],
                "source_content_reconstruction",
            )
