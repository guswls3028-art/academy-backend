from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "Production preflight found no non-positive maximum scores, negative pass scores, "
    "or enabled retakes below two attempts before adding these validation constraints."
)


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0022_examasset_teacher_explanation_source"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="exam",
            constraint=models.CheckConstraint(
                condition=models.Q(("max_score__gt", 0)),
                name="exams_exam_max_score_gt_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="exam",
            constraint=models.CheckConstraint(
                condition=models.Q(("pass_score__gte", 0)),
                name="exams_exam_pass_score_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="exam",
            constraint=models.CheckConstraint(
                condition=models.Q(("allow_retake", False), ("max_attempts__gte", 2), _connector="OR"),
                name="exams_exam_retake_attempts_gte_2",
            ),
        ),
    ]
