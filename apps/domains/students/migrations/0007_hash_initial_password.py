"""
Hash existing plaintext initial_password values in StudentRegistrationRequest.
Also widen the column to max_length=256 to accommodate hash strings.
"""
from django.db import migrations, models
from django.contrib.auth.hashers import identify_hasher, make_password


def hash_plaintext_passwords(apps, schema_editor):
    """Convert any remaining plaintext passwords to hashed form."""
    Req = apps.get_model("students", "StudentRegistrationRequest")
    for req in Req.objects.all().iterator(chunk_size=500):
        pw = req.initial_password or ""
        if not pw:
            continue
        # Check if already hashed (Django hashers start with algorithm$)
        try:
            identify_hasher(pw)
            continue  # already hashed
        except ValueError:
            pass
        req.initial_password = make_password(pw)
        req.save(update_fields=["initial_password"])


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0006_add_profile_photo_r2_key"),
    ]

    operations = [
        migrations.AlterField(
            model_name="studentregistrationrequest",
            name="initial_password",
            field=models.CharField(max_length=256),
        ),
        migrations.RunPython(
            hash_plaintext_passwords,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
