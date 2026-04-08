# apps/support/messaging/migrations/0017_clear_old_solapi_template_ids.py
"""
구 개별 Solapi 템플릿 ID 정리.

통합 4종(KA01TP260406...) 전환 완료.
시스템 기본양식(signup 카테고리: 가입승인/비번찾기)만 자체 solapi_template_id 유지.
나머지 카테고리의 solapi_template_id는 더 이상 사용하지 않으므로 정리.
"""

from django.db import migrations


# 시스템 기본양식 — solapi_template_id 유지해야 하는 카테고리
KEEP_CATEGORIES = {"signup"}

# 통합 4종 Solapi 템플릿 ID — 절대 제거하지 않음
UNIFIED_IDS = {
    "KA01TP2604061058318608Hy40ZnTFZT",  # clinic_info
    "KA01TP260406110706969XS06XRZveEk",  # clinic_change
    "KA01TP260406105458211774JKJ3OU55",  # score
    "KA01TP260406121126868FGddLmrDFUC",  # attendance
}


def clear_old_template_ids(apps, schema_editor):
    MessageTemplate = apps.get_model("messaging", "MessageTemplate")

    # signup 제외, 통합 4종 ID 제외, solapi_template_id가 있는 템플릿만 대상
    old_templates = MessageTemplate.objects.exclude(
        category__in=KEEP_CATEGORIES,
    ).exclude(
        solapi_template_id__in=UNIFIED_IDS,
    ).exclude(
        solapi_template_id="",
    )

    count = old_templates.count()
    if count > 0:
        # solapi_template_id 제거 + status 초기화
        old_templates.update(solapi_template_id="", solapi_status="")

    print(f"  Cleared {count} old solapi_template_id(s) (kept signup + unified 4)")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0016_add_clinic_result_trigger"),
    ]

    operations = [
        migrations.RunPython(clear_old_template_ids, noop),
    ]
