# Generated manually for multi-tenant: allow same username in different tenants
# Staff.user: OneToOneField -> ForeignKey, add UniqueConstraint (tenant, user)

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("staffs", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="staff",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="staff_profiles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="staff",
            constraint=models.UniqueConstraint(
                condition=models.Q(("user__isnull", False)),
                fields=("tenant", "user"),
                name="uniq_staff_user_per_tenant",
            ),
        ),
    ]
