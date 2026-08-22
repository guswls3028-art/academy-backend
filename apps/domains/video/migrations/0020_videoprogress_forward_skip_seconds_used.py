from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0019_video_order_and_folder_uniqueness"),
    ]

    operations = [
        migrations.AddField(
            model_name="videoprogress",
            name="forward_skip_seconds_used",
            field=models.PositiveIntegerField(
                db_default=0,
                default=0,
                help_text="온라인 수업 대체 모드에서 서버가 승인한 앞으로 건너뛰기 누적 초",
            ),
        ),
    ]
