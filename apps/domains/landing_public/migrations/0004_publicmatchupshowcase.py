# Generated for PublicMatchupShowcase (적중보고서 게시판) — 2026-05-13

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0039_tenant_pass_fail_labels"),
        ("landing_public", "0003_publicexamshowcase"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicMatchupShowcase",
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
                    "hit_report_id_ref",
                    models.PositiveIntegerField(
                        blank=True,
                        db_index=True,
                        help_text="원본 MatchupHitReport.id (참조용, FK 아님)",
                        null=True,
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        help_text="게시물 제목 (학원장 자유 입력, 기본=원본 보고서 제목)",
                        max_length=200,
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True, default="", help_text="학원장 코멘트 (선택)"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "초안"),
                            ("published", "공개"),
                            ("expired", "기간 만료 (카드만 노출)"),
                            ("hidden", "비공개"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=12,
                    ),
                ),
                (
                    "published_at",
                    models.DateTimeField(
                        blank=True, db_index=True, null=True,
                        help_text="공개 시작 시각. null=즉시 공개 (PUBLISHED 진입 시 now() backfill).",
                    ),
                ),
                (
                    "published_until",
                    models.DateTimeField(
                        blank=True, db_index=True, null=True,
                        help_text="공개 종료 시각. past 시 외부엔 카드 요약만 노출. null=무기한.",
                    ),
                ),
                (
                    "snapshot_pdf_key",
                    models.CharField(blank=True, default="", max_length=512),
                ),
                ("snapshot_pdf_bytes", models.PositiveIntegerField(default=0)),
                ("snapshot_meta", models.JSONField(blank=True, default=dict)),
                (
                    "snapshot_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("view_count", models.PositiveIntegerField(default=0)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_public_matchup_showcases",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="public_matchup_showcases",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "landing_public_matchup_showcase",
                "ordering": ["-published_at", "-created_at"],
                "indexes": [
                    models.Index(
                        fields=["tenant", "status", "-published_at"],
                        name="lp_mshow_tn_st_pa_idx",
                    ),
                    models.Index(
                        fields=["tenant", "hit_report_id_ref"],
                        name="lp_mshow_tn_hr_idx",
                    ),
                ],
            },
        ),
    ]
