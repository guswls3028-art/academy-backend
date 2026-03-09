# regular 시험을 template 없이 생성 가능하도록 제약 제거 (생성 후 시험 설정에서 템플릿 지정)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0003_add_exam_status"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="exam",
            name="exams_exam_regular_requires_template_exam",
        ),
    ]
