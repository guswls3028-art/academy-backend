import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "Add isolated parent-facing feedback and immutable revision history without changing existing memos."


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0020_scoreeditdraft_client_id"),
        ("enrollment", "0003_enrollment_lecture_memo"),
        ("lectures", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(name="ParentAssessmentComment", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("assessment_kind", models.CharField(max_length=8, choices=[("exam", "Exam"), ("homework", "Homework")])),
            ("assessment_id", models.PositiveBigIntegerField()),
            ("text", models.TextField(blank=True, default="", validators=[django.core.validators.MaxLengthValidator(2000)])),
            ("version", models.PositiveIntegerField(default=0)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.tenant")),
            ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="lectures.session")),
            ("enrollment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="enrollment.enrollment")),
            ("updated_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ], options={"constraints": [models.UniqueConstraint(fields=("tenant", "session", "enrollment", "assessment_kind", "assessment_id"), name="uniq_parent_assessment_comment")]}),
        migrations.CreateModel(name="ParentAssessmentCommentRevision", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("version", models.PositiveIntegerField()), ("text", models.TextField()),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("author", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ("comment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="results.parentassessmentcomment")),
        ], options={"ordering": ["-version"], "constraints": [models.UniqueConstraint(fields=("comment", "version"), name="uniq_parent_comment_revision")]}),
    ]
