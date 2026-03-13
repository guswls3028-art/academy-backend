# Generated data migration: fix parent User records with tenant_id=NULL
# Root cause: legacy parent user creation did not set User.tenant_id.
# Fix: copy tenant_id from the Parent record that owns each User.

from django.db import migrations


def fix_null_tenant_parent_users(apps, schema_editor):
    """Set User.tenant_id from Parent.tenant_id for all parent users missing it."""
    User = apps.get_model("core", "User")
    Parent = apps.get_model("parents", "Parent")

    null_users = User.objects.filter(is_active=True, tenant_id__isnull=True)
    fixed = 0
    for user in null_users:
        parent = Parent.objects.filter(user=user).first()
        if parent and parent.tenant_id:
            user.tenant_id = parent.tenant_id
            user.save(update_fields=["tenant_id"])
            fixed += 1

    if fixed:
        print(f"\n  [0018] Fixed {fixed} parent users with NULL tenant_id")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_rename_plans_standard_pro_max"),
    ]

    operations = [
        migrations.RunPython(fix_null_tenant_parent_users, noop),
    ]
