from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("lectures", "0009_session_score_totals"), ("enrollment", "0003_enrollment_lecture_memo")]
    operations = [
        migrations.CreateModel(name="SessionSchedule", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("label", models.CharField(max_length=40)),
            ("date", models.DateField()),
            ("start_time", models.TimeField(blank=True, null=True)),
            ("end_time", models.TimeField(blank=True, null=True)),
            ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="schedules", to="lectures.session")),
        ], options={
            "ordering": ["date", "start_time", "id"],
            "constraints": [models.UniqueConstraint(fields=("session", "label"), name="uniq_session_schedule_label")],
        }),
        migrations.CreateModel(name="SessionScheduleMembership", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("schedule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="lectures.sessionschedule")),
            ("session_enrollment", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="schedule_membership", to="enrollment.sessionenrollment")),
        ]),
        migrations.CreateModel(name="SessionScheduleChange", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("actor_id", models.BigIntegerField(null=True)),
            ("before", models.JSONField(default=dict)),
            ("after", models.JSONField(default=dict)),
            ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="schedule_changes", to="lectures.session")),
        ]),
    ]
