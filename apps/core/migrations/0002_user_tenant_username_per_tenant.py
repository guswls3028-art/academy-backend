# 1테넌트 1프로그램: User에 tenant FK 추가. username은 tenant 소속 시 내부적으로 t{id}_{로그인아이디} 저장해 전역 유일 유지.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="tenant",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="users",
                to="core.tenant",
            ),
        ),
    ]
