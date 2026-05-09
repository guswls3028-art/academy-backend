# Generated for V11 BOTTLENECK §7.1 — fine-tune loop base on 2026-05-10
# AutoSegmentationSnapshot 신설. 자동 cut 결과 audit + manual diff 학습 신호.
#
# blast radius:
# - 신규 모델 CreateModel 만. 기존 모델 / 인덱스 / 제약 변경 0.
# - callback _handle_matchup_ai_result 가 bulk_create instrument 추가 — 운영 path
#   변경 0 (try/except fail-soft).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0020_matchuppagestate"),
    ]

    operations = [
        migrations.CreateModel(
            name="AutoSegmentationSnapshot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "job_id",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=64
                    ),
                ),
                ("page_index", models.PositiveIntegerField(db_index=True)),
                ("detected_problem_number", models.IntegerField(default=0)),
                ("bbox", models.JSONField(blank=True, default=dict)),
                (
                    "engine",
                    models.CharField(
                        choices=[
                            ("yolo", "YOLO segmentation"),
                            ("yolo_v11", "YOLO V11"),
                            ("yolo_v12", "YOLO V12"),
                            ("yolo_v13", "YOLO V13"),
                            ("vlm", "VLM (Gemini)"),
                            ("ocr", "OCR + layout heuristic"),
                            ("native_pdf", "Native PDF parser"),
                            ("hybrid", "Hybrid (YOLO + VLM verifier)"),
                            ("manual_assist", "사용자 수동 자르기 보조"),
                            ("unknown", "엔진 미상"),
                        ],
                        db_index=True,
                        default="unknown",
                        max_length=32,
                    ),
                ),
                (
                    "engine_version",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("confidence", models.FloatField(default=0.0)),
                ("class_id", models.IntegerField(default=0)),
                (
                    "class_name",
                    models.CharField(default="problem", max_length=32),
                ),
                (
                    "post_process_stage",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="auto_segmentation_snapshots",
                        to="matchup.matchupdocument",
                    ),
                ),
                (
                    "promoted_problem",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="source_snapshots",
                        to="matchup.matchupproblem",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="auto_segmentation_snapshots",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["document_id", "page_index", "detected_problem_number"],
            },
        ),
        migrations.AddIndex(
            model_name="autosegmentationsnapshot",
            index=models.Index(
                fields=["tenant", "document", "page_index"],
                name="matchup_aut_tenant__page_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="autosegmentationsnapshot",
            index=models.Index(
                fields=["tenant", "engine", "engine_version"],
                name="matchup_aut_tenant__engine_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="autosegmentationsnapshot",
            index=models.Index(
                fields=["job_id"],
                name="matchup_aut_job_id_idx",
            ),
        ),
    ]
