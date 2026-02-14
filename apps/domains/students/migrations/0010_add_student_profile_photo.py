# Generated manually for Student.profile_photo (학생앱 전용 업로드)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0009_alter_student_parent_phone_alter_student_phone_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="profile_photo",
            field=models.ImageField(
                blank=True,
                help_text="학생이 학생앱에서 업로드한 프로필 사진",
                null=True,
                upload_to="student_profile/%Y/%m/",
            ),
        ),
    ]
