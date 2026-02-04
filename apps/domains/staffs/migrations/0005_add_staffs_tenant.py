# PATH: apps/domains/staffs/migrations/0005_add_staffs_tenant.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("staffs", "0004_payrollsnapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="staff",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="staffs",
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="worktype",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="work_types",
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="staffworktype",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="staff_work_types",
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="work_records",
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="expenserecord",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="expense_records",
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="workmonthlock",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="work_month_locks",
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="payrollsnapshot",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="payroll_snapshots",
                null=True,
                blank=True,
            ),
        ),
    ]
