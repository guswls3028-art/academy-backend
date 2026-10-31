import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0005_planned_arrival"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendance", name="qr_checked_in_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="AttendanceQrPoint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(max_length=100)),
                ("kind", models.CharField(choices=[("class", "수업"), ("clinic", "클리닉")], max_length=10)),
                ("location", models.CharField(blank=True, default="", max_length=100)),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("lecture", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to="lectures.lecture")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.tenant")),
            ],
            options={
                "ordering": ["-id"],
                "constraints": [models.CheckConstraint(
                    condition=(models.Q(kind="class", lecture__isnull=False, location="")
                               | models.Q(kind="clinic", lecture__isnull=True)),
                    name="attendance_qr_valid_scope",
                )],
            },
        ),
    ]
