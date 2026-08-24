from django.db import migrations, models


def backfill_lecture_display_order(apps, schema_editor):
    Lecture = apps.get_model("lectures", "Lecture")
    tenant_ids = (
        Lecture.objects.order_by()
        .values_list("tenant_id", flat=True)
        .distinct()
    )
    for tenant_id in tenant_ids.iterator():
        lecture_ids = list(
            Lecture.objects.filter(tenant_id=tenant_id)
            .order_by("-created_at", "-id")
            .values_list("id", flat=True)
        )
        for display_order, lecture_id in enumerate(lecture_ids, start=1):
            Lecture.objects.filter(pk=lecture_id).update(
                display_order=display_order
            )


class Migration(migrations.Migration):
    dependencies = [
        ("lectures", "0007_session_regular_order_session_session_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="lecture",
            name="display_order",
            field=models.PositiveIntegerField(
                blank=True,
                editable=False,
                help_text="학원 내 강의 목록의 영구 수동 순서",
                null=True,
            ),
        ),
        migrations.RunPython(
            backfill_lecture_display_order,
            migrations.RunPython.noop,
        ),
    ]
