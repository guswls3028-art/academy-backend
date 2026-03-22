"""
Backfill Exam.tenant for existing rows.

Strategy:
1. Regular exams: sessions → lecture → tenant (direct path)
2. Template exams with derived_exams: derived_exams → sessions → lecture → tenant
3. Remaining orphan templates: leave null (will be set on next access)
"""
from django.db import migrations


def backfill_tenant(apps, schema_editor):
    Exam = apps.get_model("exams", "Exam")

    # 1. Regular exams: get tenant from sessions → lecture
    for exam in Exam.objects.filter(tenant__isnull=True, exam_type="regular"):
        session = exam.sessions.select_related("lecture").first()
        if session and session.lecture and session.lecture.tenant_id:
            exam.tenant_id = session.lecture.tenant_id
            exam.save(update_fields=["tenant_id"])

    # 2. Template exams: get tenant from derived regular exams
    for exam in Exam.objects.filter(tenant__isnull=True, exam_type="template"):
        derived = exam.derived_exams.filter(tenant__isnull=False).first()
        if derived:
            exam.tenant_id = derived.tenant_id
            exam.save(update_fields=["tenant_id"])
            continue
        # Try through derived exams' sessions
        for derived_exam in exam.derived_exams.all():
            session = derived_exam.sessions.select_related("lecture").first()
            if session and session.lecture and session.lecture.tenant_id:
                exam.tenant_id = session.lecture.tenant_id
                exam.save(update_fields=["tenant_id"])
                break


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0008_add_tenant_fk_to_exam"),
    ]

    operations = [
        migrations.RunPython(backfill_tenant, migrations.RunPython.noop),
    ]
