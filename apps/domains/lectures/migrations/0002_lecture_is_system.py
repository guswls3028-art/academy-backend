"""
Add is_system flag to Lecture model.
System lectures (e.g. 전체공개영상 container) are excluded from
lecture lists, grades, and other student-facing features by this flag
instead of fragile string matching on title.

Also adds a partial unique constraint to ensure at most one system lecture per tenant.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lectures", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="lecture",
            name="is_system",
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text="시스템용 강의 (전체공개영상 컨테이너 등). 강의 목록·성적 등에서 자동 제외.",
            ),
        ),
        # 테넌트당 시스템 강의 최대 1개 보장
        migrations.AddConstraint(
            model_name="lecture",
            constraint=models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_system=True),
                name="uniq_system_lecture_per_tenant",
            ),
        ),
    ]
