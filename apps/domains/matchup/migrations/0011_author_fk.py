# 매치업 보고서 = 강사 1인의 누적 포트폴리오 정체성 도입.
#   1. MatchupDocument.author (FK → User, SET_NULL, nullable):
#      누가 업로드/소유한 자료인지. find_similar_problems 강사 격리의 baseline.
#   2. MatchupHitReport.author (FK → User, SET_NULL, nullable):
#      보고서 작성자. submitted_by_id IntegerField는 deprecated (호환 보존).
#   3. MatchupHitReport.document: OneToOneField → ForeignKey (related_name 복수화).
#      카테고리당 시험지 1장에 강사 N명이 각자 보고서 작성 가능.
#   4. UniqueConstraint(document, author): 같은 강사가 같은 시험지에 보고서 2건 작성 차단.
#      author=NULL은 PostgreSQL NULL semantics로 자동 면제 — legacy report 보호.
#   5. RunPython 백필: submitted_by_id → author FK.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_report_author(apps, schema_editor):
    """기존 submitted_by_id가 가리키는 user를 author FK로 옮긴다.

    submitted_by_id가 비어있거나 user가 삭제된 경우 author=NULL 유지.
    user 무결성 차원에서 실패해도 silent하게 None — 운영 데이터 손실 방지.
    """
    HitReport = apps.get_model("matchup", "MatchupHitReport")
    User = apps.get_model(settings.AUTH_USER_MODEL.split(".")[0],
                          settings.AUTH_USER_MODEL.split(".")[1])
    user_ids = set(
        User.objects.values_list("id", flat=True)
    )
    for report in HitReport.objects.filter(
        submitted_by_id__isnull=False, author__isnull=True
    ).iterator():
        if report.submitted_by_id in user_ids:
            report.author_id = report.submitted_by_id
            report.save(update_fields=["author"])


def noop_reverse(apps, schema_editor):
    # author FK는 데이터 손실 위험 없으므로 reverse는 noop.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0010_hit_report"),
        # User 모델 이전 마이그레이션이 먼저 적용되어 있어야 FK 가능.
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Document.author 추가
        migrations.AddField(
            model_name="matchupdocument",
            name="author",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True,
                related_name="matchup_documents_authored",
                db_index=True,
                help_text="자료 업로더(소유 강사). NULL=legacy/공용 풀 — find_similar에서 모든 강사가 후보로 사용 가능.",
            ),
        ),
        # 2. HitReport.author 추가
        migrations.AddField(
            model_name="matchuphitreport",
            name="author",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True,
                related_name="matchup_hit_reports_authored",
                db_index=True,
                help_text="보고서 작성 강사. submitted_by_id는 deprecated (호환 보존).",
            ),
        ),
        # 3. submitted_by_id → author 백필
        migrations.RunPython(backfill_report_author, reverse_code=noop_reverse),
        # 4. document OneToOneField → ForeignKey (unique 제약 drop). related_name 복수화.
        migrations.AlterField(
            model_name="matchuphitreport",
            name="document",
            field=models.ForeignKey(
                to="matchup.matchupdocument",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="hit_reports",
            ),
        ),
        # 5. UniqueConstraint(document, author)
        migrations.AddConstraint(
            model_name="matchuphitreport",
            constraint=models.UniqueConstraint(
                fields=("document", "author"),
                name="unique_hit_report_doc_author",
            ),
        ),
    ]
