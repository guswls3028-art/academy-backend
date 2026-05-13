# Generated for PublicPostLike.unique_together tenant 추가 — P0 audit fix (2026-05-13).
# 이전: (user, target_kind, target_id) → cross-tenant 시도 시 UNIQUE violation.
# 이후: (tenant, user, target_kind, target_id) — 학원별 격리.
#
# 안전성: 기존 row가 같은 (user, target_kind, target_id) cross-tenant duplicate 가
# 있을 가능성은 매우 낮음 (한 user가 동시에 여러 학원 family인 케이스만). production
# 대부분의 user는 단일 tenant. 따라서 새 constraint apply 시 violation 거의 없음.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("landing_public", "0004_publicmatchupshowcase"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="publicpostlike",
            unique_together={("tenant", "user", "target_kind", "target_id")},
        ),
    ]
