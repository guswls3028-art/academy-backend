from django.db import migrations, models


def preserve_existing_teacher_titles(apps, schema_editor):
    Staff = apps.get_model("staffs", "Staff")
    TenantMembership = apps.get_model("core", "TenantMembership")
    Teacher = apps.get_model("teachers", "Teacher")

    teacher_user_ids = TenantMembership.objects.filter(
        role="teacher",
        is_active=True,
    ).values_list("user_id", flat=True)
    Staff.objects.filter(user_id__in=teacher_user_ids).update(
        position="INSTRUCTOR",
    )

    legacy_teacher_keys = {
        (tenant_id, name, phone or "")
        for tenant_id, name, phone in Teacher.objects.filter(
            is_active=True
        ).values_list("tenant_id", "name", "phone")
    }
    legacy_staff = Staff.objects.filter(user__isnull=True).only(
        "id",
        "tenant_id",
        "name",
        "phone",
    )
    instructor_ids = [
        staff.id
        for staff in legacy_staff.iterator()
        if (staff.tenant_id, staff.name, staff.phone or "")
        in legacy_teacher_keys
    ]
    if instructor_ids:
        Staff.objects.filter(id__in=instructor_ids).update(
            position="INSTRUCTOR",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0058_tenant_controls_messaging_activation"),
        ("staffs", "0008_payrollsnapshot_staff_name"),
        ("teachers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="staff",
            name="position",
            field=models.CharField(
                choices=[
                    ("DIRECTOR", "실장"),
                    ("INSTRUCTOR", "강사"),
                    ("ASSISTANT", "조교"),
                    ("STAFF", "직원"),
                ],
                default="ASSISTANT",
                db_default="ASSISTANT",
                help_text=(
                    "조직에서 사용하는 표시 직위. "
                    "계정 역할·관리 권한과 별개입니다."
                ),
                max_length=20,
            ),
        ),
        migrations.RunPython(
            preserve_existing_teacher_titles,
            migrations.RunPython.noop,
        ),
    ]
