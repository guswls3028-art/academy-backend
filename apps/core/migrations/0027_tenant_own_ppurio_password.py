from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_rename_ppurio_display_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="own_ppurio_password",
            field=models.CharField(
                blank=True,
                default="",
                help_text="뿌리오 로그인 비밀번호 (향후 필요 시)",
                max_length=200,
            ),
        ),
    ]
