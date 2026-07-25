from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_login_throttle_bucket"),
    ]

    operations = [
        migrations.AlterField(
            model_name="program",
            name="plan",
            field=models.CharField(
                choices=[
                    ("standard", "Standard (전환 호환)"),
                    ("pro", "Pro (전환 호환)"),
                    ("max", "Max (전환 호환)"),
                    ("all", "전체 기능"),
                ],
                default="pro",
                help_text="단일 요금제 전환 호환 필드",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="program",
            name="monthly_price",
            field=models.PositiveIntegerField(
                default=198000,
                help_text="단일 요금제 전환 호환 공급가 필드",
            ),
        ),
    ]
