from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("matchup", "0023_matchupdocument_exam_cycle_matchupdocument_exam_year"),
    ]

    operations = [
        migrations.AddField(
            model_name="problemsegmentationproposal",
            name="proposal_kind",
            field=models.CharField(
                choices=[
                    ("segmentation", "문항 분리 제안"),
                    ("manual_index", "수동 문항 AI 인덱싱 제안"),
                ],
                db_index=True,
                default="segmentation",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="problemsegmentationproposal",
            name="target_problem",
            field=models.ForeignKey(
                blank=True,
                help_text="manual_index 제안이 승인 시 갱신할 수동 문항. callback은 이 행을 수정하지 않는다.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="index_proposals",
                to="matchup.matchupproblem",
            ),
        ),
        migrations.AddConstraint(
            model_name="problemsegmentationproposal",
            constraint=models.UniqueConstraint(
                condition=models.Q(("proposal_kind", "manual_index")),
                fields=("tenant", "target_problem", "analysis_version_key"),
                name="uniq_manual_index_job_target",
            ),
        ),
    ]
