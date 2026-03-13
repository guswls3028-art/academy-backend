"""Update Max plan price 300,000 → 330,000."""
from django.db import migrations


def update_max_price(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    updated = Program.objects.filter(plan="max", monthly_price=300000).update(monthly_price=330000)
    if updated:
        print(f"\n  [0019] Updated {updated} max plans: 300,000 → 330,000")


def revert_max_price(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    Program.objects.filter(plan="max", monthly_price=330000).update(monthly_price=300000)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_fix_parent_user_null_tenant"),
    ]
    operations = [
        migrations.RunPython(update_max_price, revert_max_price),
    ]
