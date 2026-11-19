from django.conf import settings
from django.db import migrations, models


def assign_existing_teachers(apps, schema_editor):
    database = schema_editor.connection.alias
    links = apps.get_model("students", "ClassGroupLecture").objects.using(database)
    memberships = apps.get_model("core", "TenantMembership").objects.using(database)
    teachers = {}
    for name, user_id in memberships.filter(tenant_id=719, tenant__code="dawon", role="teacher",
                                            is_active=True, user__is_active=True).values_list("user__name", "user_id"):
        if name:
            teachers[name] = user_id if name not in teachers else None
    for link in links.filter(group__tenant_id=719, lecture__tenant_id=719, group__tenant__code="dawon").select_related("lecture"):
        teacher = teachers.get(link.lecture.name)
        if teacher is not None:
            links.filter(pk=link.pk).update(reference_teacher_id=teacher)


class Migration(migrations.Migration):
    dependencies = [("students", "0022_class_groups"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name="classgrouplecture", name="top_reference_percent",
                            field=models.PositiveSmallIntegerField(default=30, db_default=30)),
        migrations.AddField(model_name="classgrouplecture", name="reference_teacher",
                            field=models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, blank=True,
                                                    on_delete=models.SET_NULL, related_name="+")),
        migrations.RunPython(assign_existing_teachers, migrations.RunPython.noop),
        migrations.AddConstraint(model_name="classgrouplecture", constraint=models.CheckConstraint(
            condition=models.Q(top_reference_percent__range=(1, 100)), name="students_group_reference_percent")),
    ]
