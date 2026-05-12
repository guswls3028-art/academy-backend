# 2026-05-12 — Tenant 모델에 누가 video_max_devices/video_max_sessions 필드를
# 추가했지만 누락된 migration. landing_public 도메인 검증 시 dev DB select 에러로
# 노출되어 복원. 본 PR scope는 아니지만 dev DB 일관성을 위해 묶어둠.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_landing_testimonial"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="landingconsultrequest",
            new_name="core_landin_tenant__24890c_idx",
            old_name="core_landin_tenant__c8a3a4_idx",
        ),
        migrations.RenameIndex(
            model_name="landingconsultrequest",
            new_name="core_landin_tenant__005419_idx",
            old_name="core_landin_tenant__a3b1c5_idx",
        ),
        migrations.RenameIndex(
            model_name="landingtestimonialsubmission",
            new_name="core_landin_tenant__0df510_idx",
            old_name="core_landin_tenant__b9c4a2_idx",
        ),
        migrations.AddField(
            model_name="tenant",
            name="video_max_devices",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="학생 1인당 동시 디바이스 수. 0=제한 없음. 1~10=제한.",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="video_max_sessions",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="학생 1인당 동시 재생 세션 수. 0=제한 없음(권장: 명시 ON 전까지). 1~10=제한.",
            ),
        ),
        migrations.AlterField(
            model_name="landingconsultrequest",
            name="id",
            field=models.BigAutoField(
                auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
            ),
        ),
        migrations.AlterField(
            model_name="landingtestimonialsubmission",
            name="id",
            field=models.BigAutoField(
                auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
            ),
        ),
    ]
