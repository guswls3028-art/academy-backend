from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_max_plan_price_330000"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="og_title",
            field=models.CharField(
                blank=True, default="", max_length=100,
                help_text="카카오톡/SNS 링크 미리보기 제목 (비어 있으면 학원명 사용)",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="og_description",
            field=models.CharField(
                blank=True, default="", max_length=300,
                help_text="카카오톡/SNS 링크 미리보기 설명",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="og_image_url",
            field=models.CharField(
                blank=True, default="", max_length=500,
                help_text="카카오톡/SNS 링크 미리보기 이미지 URL",
            ),
        ),
    ]
