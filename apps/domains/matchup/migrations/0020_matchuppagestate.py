# Generated for MVP Phase A — page-level state (auto/skip/manual) on 2026-05-09
# 사용자 directive (basic_definition_2026_05_09): 합격선 = 'AI 가 완벽히 자동 cut'
# 이 아니라 '최종 Problem Image Set 을 학원장이 최소 노동으로 확정'.
# 그 1단계 = page-level 분기 (auto/skip/manual).
#
# blast radius:
# - 신규 모델 CreateModel 만. 기존 모델 / 인덱스 / 제약 변경 0.
# - MatchupDocument.meta.excluded_pages SSOT 그대로 유지 — 신규 모델은 부가 audit/UI 용.
# - sync helper (services 측) 가 단방향 (state='skip' → meta.excluded_pages) 갱신.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0019_layoutfingerprint_manualcorrectiondelta_and_more"),
        ("core", "0035_workerheartbeatmodel"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MatchupPageState",
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
                ("page_index", models.PositiveIntegerField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("auto", "자동 분리"),
                            ("skip", "건너뛰기"),
                            ("manual", "직접 자르기만"),
                        ],
                        db_index=True,
                        default="auto",
                        max_length=10,
                    ),
                ),
                (
                    "auto_reason",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="자동 state 추천 근거. 학원장 수동 변경 시 클리어.",
                        max_length=64,
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="page_states",
                        to="matchup.matchupdocument",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="matchup_page_states",
                        to="core.tenant",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="마지막 변경 사용자. NULL=시스템 자동 추천.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["document_id", "page_index"],
            },
        ),
        migrations.AddConstraint(
            model_name="matchuppagestate",
            constraint=models.UniqueConstraint(
                fields=("document", "page_index"),
                name="unique_matchup_page_state",
            ),
        ),
        migrations.AddIndex(
            model_name="matchuppagestate",
            index=models.Index(
                fields=["tenant", "document"],
                name="matchup_pag_tenant__page_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="matchuppagestate",
            index=models.Index(
                fields=["tenant", "state"],
                name="matchup_pag_tenant__state_idx",
            ),
        ),
    ]
