from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("matchup", "0009_matchupproblem_image_embedding"),
    ]

    operations = [
        migrations.CreateModel(
            name="MatchupHitReport",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("summary", models.TextField(blank=True, default="")),
                ("status", models.CharField(
                    choices=[("draft", "작성중"), ("submitted", "제출됨")],
                    db_index=True, default="draft", max_length=20,
                )),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("submitted_by_id", models.IntegerField(blank=True, null=True)),
                ("submitted_by_name", models.CharField(blank=True, default="", max_length=100)),
                ("document", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="hit_report",
                    to="matchup.matchupdocument",
                )),
                ("tenant", models.ForeignKey(
                    db_index=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="matchup_hit_reports",
                    to="core.tenant",
                )),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="MatchupHitReportEntry",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("selected_problem_ids", models.JSONField(blank=True, default=list)),
                ("comment", models.TextField(blank=True, default="")),
                ("order", models.PositiveIntegerField(default=0)),
                ("exam_problem", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="hit_report_entries",
                    to="matchup.matchupproblem",
                )),
                ("report", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="entries",
                    to="matchup.matchuphitreport",
                )),
                ("tenant", models.ForeignKey(
                    db_index=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="matchup_hit_report_entries",
                    to="core.tenant",
                )),
            ],
            options={"ordering": ["order", "id"]},
        ),
        migrations.AddConstraint(
            model_name="matchuphitreportentry",
            constraint=models.UniqueConstraint(
                fields=("report", "exam_problem"),
                name="unique_hit_report_exam_problem",
            ),
        ),
    ]
