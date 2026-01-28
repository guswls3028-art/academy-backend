# PATH: apps/domains/homework_results/migrations/0006_backfill_homework_fk_first_homework.py
from django.db import migrations


def forwards(apps, schema_editor):
    Homework = apps.get_model("homework_results", "Homework")
    HomeworkScore = apps.get_model("homework_results", "HomeworkScore")

    # 세션별 첫 과제 map
    first_hw_by_session = {}
    for hw in Homework.objects.order_by("session_id", "id").all():
        sid = hw.session_id
        if sid not in first_hw_by_session:
            first_hw_by_session[sid] = hw.id

    # homework가 비어있는 기존 점수만 채움
    qs = HomeworkScore.objects.filter(homework__isnull=True).all()
    for obj in qs.iterator():
        hw_id = first_hw_by_session.get(obj.session_id)
        if hw_id:
            obj.homework_id = hw_id
            obj.save(update_fields=["homework"])


def backwards(apps, schema_editor):
    HomeworkScore = apps.get_model("homework_results", "HomeworkScore")
    HomeworkScore.objects.update(homework=None)


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0005_add_homework_fk_nullable"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
