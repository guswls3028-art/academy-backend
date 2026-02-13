# ScopeNode 백필: 기존 Lecture/Session당 노드 1개씩 생성

from django.db import migrations


def backfill_scope_nodes(apps, schema_editor):
    ScopeNode = apps.get_model("community", "ScopeNode")
    Lecture = apps.get_model("lectures", "Lecture")
    Session = apps.get_model("lectures", "Session")

    for lecture in Lecture.objects.all():
        ScopeNode.objects.get_or_create(
            tenant_id=lecture.tenant_id,
            lecture_id=lecture.id,
            session_id=None,
            defaults={
                "level": "COURSE",
                "parent_id": None,
            },
        )

    course_nodes = {
        (n.tenant_id, n.lecture_id): n
        for n in ScopeNode.objects.filter(session_id__isnull=True)
    }
    for session in Session.objects.select_related("lecture").all():
        key = (session.lecture.tenant_id, session.lecture_id)
        parent = course_nodes.get(key)
        if not parent:
            parent, _ = ScopeNode.objects.get_or_create(
                tenant_id=session.lecture.tenant_id,
                lecture_id=session.lecture_id,
                session_id=None,
                defaults={"level": "COURSE", "parent_id": None},
            )
            course_nodes[key] = parent
        ScopeNode.objects.get_or_create(
            tenant_id=session.lecture.tenant_id,
            lecture_id=session.lecture_id,
            session_id=session.id,
            defaults={
                "level": "SESSION",
                "parent_id": parent.id,
            },
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0001_initial"),
        ("lectures", "0006_alter_lecture_lecture_time_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_scope_nodes, noop),
    ]
