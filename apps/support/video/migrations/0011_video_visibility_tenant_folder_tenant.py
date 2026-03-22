"""
Domain model fix: separate 'public video' concept from Lecture entity.

Schema changes:
- Video: add visibility (ENROLLED/PUBLIC), tenant FK
- VideoFolder: add tenant FK, make session nullable,
  replace unique constraint (session, parent, name) → (tenant, parent, name)

Data migration:
- Set visibility=PUBLIC on videos under 전체공개영상 lectures
- Populate Video.tenant from session→lecture→tenant chain
- Populate VideoFolder.tenant from session→lecture→tenant chain
- Set Lecture.is_system=True on 전체공개영상 lectures
"""

from django.db import migrations, models
import django.db.models.deletion


def populate_visibility_and_tenant(apps, schema_editor):
    """
    1. Mark videos under '전체공개영상' lectures as PUBLIC.
    2. Set tenant on ALL videos from their session→lecture→tenant chain.
    3. Set tenant on ALL video folders.
    4. Set is_system=True on '전체공개영상' lectures.

    NOTE: apps.get_model() uses Django's default Manager (not custom VideoManager),
    so soft-deleted videos (deleted_at IS NOT NULL) are also covered.
    """
    Video = apps.get_model("video", "Video")
    VideoFolder = apps.get_model("video", "VideoFolder")
    Lecture = apps.get_model("lectures", "Lecture")
    Session = apps.get_model("lectures", "Session")

    # Step 1: Find all 전체공개영상 lectures and mark them as system
    public_lectures = Lecture.objects.filter(title="전체공개영상")
    public_lecture_ids = set(public_lectures.values_list("id", flat=True))
    public_lectures.update(is_system=True)

    # Step 2: Find all sessions under public lectures
    public_session_ids = set(
        Session.objects.filter(lecture_id__in=public_lecture_ids)
        .values_list("id", flat=True)
    )

    # Step 3: Set visibility=PUBLIC on videos in public sessions (including soft-deleted)
    if public_session_ids:
        Video.objects.filter(session_id__in=public_session_ids).update(
            visibility="PUBLIC"
        )

    # Step 4: Populate tenant on ALL videos (batch by session→lecture→tenant)
    # Build session_id → tenant_id mapping
    session_tenant_map = {}
    for s in Session.objects.select_related("lecture").all():
        session_tenant_map[s.id] = s.lecture.tenant_id

    # Batch update by tenant_id to minimize queries
    from collections import defaultdict
    tenant_to_video_sessions = defaultdict(list)
    for session_id, tenant_id in session_tenant_map.items():
        tenant_to_video_sessions[tenant_id].append(session_id)

    for tenant_id, session_ids in tenant_to_video_sessions.items():
        Video.objects.filter(
            session_id__in=session_ids, tenant_id__isnull=True
        ).update(tenant_id=tenant_id)

    # Step 5: Populate tenant on ALL video folders
    for session_id, tenant_id in session_tenant_map.items():
        VideoFolder.objects.filter(
            session_id=session_id, tenant_id__isnull=True
        ).update(tenant_id=tenant_id)

    # Step 6: Handle orphaned videos (session_id=NULL) — skip, tenant stays NULL
    # These are videos whose session was deleted (SET_NULL). They have no tenant
    # derivation path. They remain tenant=NULL and will be picked up by
    # the soft-delete purge process. Log count for visibility.
    orphan_count = Video.objects.filter(
        session_id__isnull=True, tenant_id__isnull=True
    ).count()
    if orphan_count > 0:
        import sys
        sys.stdout.write(
            f"\n  [INFO] {orphan_count} orphaned videos (session=NULL) "
            f"could not be assigned a tenant.\n"
        )


def reverse_migration(apps, schema_editor):
    """Reverse: clear visibility/tenant fields (non-destructive)."""
    Video = apps.get_model("video", "Video")
    VideoFolder = apps.get_model("video", "VideoFolder")
    Lecture = apps.get_model("lectures", "Lecture")

    Video.objects.all().update(visibility="ENROLLED", tenant_id=None)
    VideoFolder.objects.all().update(tenant_id=None)
    Lecture.objects.filter(is_system=True).update(is_system=False)


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0010_fk_tenant_conversion"),
        ("lectures", "0002_lecture_is_system"),
        ("core", "0001_initial"),
    ]

    operations = [
        # --- Video: add visibility ---
        migrations.AddField(
            model_name="video",
            name="visibility",
            field=models.CharField(
                choices=[("ENROLLED", "수강생 전용"), ("PUBLIC", "전체 공개")],
                db_index=True,
                default="ENROLLED",
                help_text="접근 범위: ENROLLED(수강생 전용) / PUBLIC(테넌트 내 전체 학생)",
                max_length=10,
            ),
        ),
        # --- Video: add tenant FK ---
        migrations.AddField(
            model_name="video",
            name="tenant",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="영상 소유 테넌트 (직접 참조, session→lecture→tenant 체인 대체)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="videos",
                to="core.tenant",
            ),
        ),
        # --- VideoFolder: add tenant FK ---
        migrations.AddField(
            model_name="videofolder",
            name="tenant",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="폴더 소유 테넌트",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="video_folders",
                to="core.tenant",
            ),
        ),
        # --- VideoFolder: make session nullable ---
        migrations.AlterField(
            model_name="videofolder",
            name="session",
            field=models.ForeignKey(
                blank=True,
                help_text="레거시: 공개 영상 세션 (신규 폴더는 tenant 직접 참조)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="video_folders",
                to="lectures.session",
            ),
        ),
        # --- VideoFolder: drop old constraint, add new one ---
        migrations.RemoveConstraint(
            model_name="videofolder",
            name="unique_video_folder_name",
        ),
        migrations.AddConstraint(
            model_name="videofolder",
            constraint=models.UniqueConstraint(
                fields=["tenant", "parent", "name"],
                name="unique_video_folder_name_per_tenant",
            ),
        ),
        # --- VideoFolder: update index ---
        migrations.RemoveIndex(
            model_name="videofolder",
            name="video_video_session_645665_idx",
        ),
        migrations.AddIndex(
            model_name="videofolder",
            index=models.Index(
                fields=["tenant", "parent"],
                name="video_videofolder_tenant_parent_idx",
            ),
        ),
        # --- Data migration ---
        migrations.RunPython(
            populate_visibility_and_tenant,
            reverse_code=reverse_migration,
        ),
    ]
