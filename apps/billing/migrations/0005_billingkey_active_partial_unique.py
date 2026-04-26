"""
BillingKey: tenant당 활성 빌링키 1개만 허용 (DB partial unique).

application-level select_for_update만으로도 race를 차단하지만, 코드 경로
우회/직접 DB 쓰기/향후 코드 변경에 대비한 defense in depth.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0004_backfill_provider_order_ids"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="billingkey",
            constraint=models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_active=True),
                name="billingkey_one_active_per_tenant",
            ),
        ),
    ]
