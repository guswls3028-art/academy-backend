# Backfill tenant_id on ClinicLink from enrollment.tenant_id
# Zero-downtime: Step 2 of 3 (backfill data)

from django.db import migrations


def backfill_tenant(apps, schema_editor):
    """
    모든 ClinicLink의 tenant_id를 enrollment.tenant_id로 채운다.
    enrollment은 NOT NULL FK이므로 모든 행이 채워짐.
    """
    migrations.RunSQL.noop  # placeholder for reverse
    ClinicLink = apps.get_model("progress", "ClinicLink")
    Enrollment = apps.get_model("enrollment", "Enrollment")

    # 배치 업데이트: 한 번에 처리 (일반적으로 수천~수만 행 규모)
    batch_size = 5000
    qs = ClinicLink.objects.filter(tenant_id__isnull=True)

    while True:
        ids = list(qs.values_list("id", flat=True)[:batch_size])
        if not ids:
            break

        # enrollment_id → tenant_id 매핑
        links = ClinicLink.objects.filter(id__in=ids).values_list("id", "enrollment_id")
        enrollment_ids = set(eid for _, eid in links)
        tenant_map = dict(
            Enrollment.objects.filter(id__in=enrollment_ids).values_list("id", "tenant_id")
        )

        for link_id, enrollment_id in links:
            tenant_id = tenant_map.get(enrollment_id)
            if tenant_id:
                ClinicLink.objects.filter(id=link_id).update(tenant_id=tenant_id)


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0007_cliniclink_add_tenant_fk"),
        ("enrollment", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_tenant, migrations.RunPython.noop),
    ]
