from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "기존 표시용 기본값을 고객이 직접 제어하는 알림톡 제품 기본값으로 전환한다."
)


def preserve_existing_messaging_choices(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")

    # 기존에는 필드가 표시용이어서 대부분 False였지만 실제 발송은 허용됐다.
    # 배포 후 갑자기 고객 발송을 막지 않도록 사용 중 학원은 켠 상태로 이관한다.
    Tenant.objects.filter(is_active=True).exclude(pk__in=(4, 9999)).update(
        messaging_is_active=True
    )
    # ymath(4)는 기존 원장 요청을 이제 숨은 코드값이 아닌 고객 설정으로 보존한다.
    Tenant.objects.filter(pk__in=(4, 9999)).update(messaging_is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0057_opsauditlog_target_user_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tenant",
            name="messaging_is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(preserve_existing_messaging_choices, migrations.RunPython.noop),
    ]
