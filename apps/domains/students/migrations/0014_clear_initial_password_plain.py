from django.db import migrations, models


def clear_initial_password_plain(apps, schema_editor):
    StudentRegistrationRequest = apps.get_model("students", "StudentRegistrationRequest")
    StudentRegistrationRequest.objects.exclude(initial_password_plain="").update(
        initial_password_plain="",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0013_student_schedule_hidden_ids"),
    ]

    operations = [
        migrations.AlterField(
            model_name="studentregistrationrequest",
            name="initial_password_plain",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Deprecated. 원문 비밀번호는 저장하지 않으며 기존 값은 마이그레이션으로 비운다.",
                max_length=128,
            ),
        ),
        migrations.RunPython(clear_initial_password_plain, migrations.RunPython.noop),
    ]
