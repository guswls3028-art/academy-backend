from django.db import migrations


# Only the four known bootstrap records, and only fields still equal to their
# original generated copy. Publisher-authored replacements are never overwritten.
LAUNCH_COPY = {
    "xgjL2HyrdFM": ("통합과학 잘하는 학생들의 공통점", "두각 INSIDE에서 나눈 신민T의 통합과학 공부 이야기.", "interview", "landscape"),
    "YYTULV4h4Io": ("AI가 스타강사를 대체할까요?", "수업과 선생님의 역할에 대한 신민T의 솔직한 생각.", "interview", "landscape"),
    "Q70aZV1TH1Y": ("통합과학, 이해일까 암기일까?", "신민T가 짧고 명확하게 들려주는 통합과학 공부법.", "lesson", "vertical"),
    "5EX6-IKy5dc": ("신민쌤의 밸런스 게임", "수업 밖에서 만나는 신민 선생님의 유쾌한 모습.", "news", "vertical"),
}


def neutralize_launch_copy(apps, schema_editor):
    Video = apps.get_model("landing_public", "PublicVideo")
    Event = apps.get_model("landing_public", "PublicVideoEvent")
    alias = schema_editor.connection.alias
    rows = Video.objects.using(alias).select_for_update().filter(tenant__code="godmin", youtube_video_id__in=LAUNCH_COPY)
    for row in rows:
        title, summary, category, video_format = LAUNCH_COPY[row.youtube_video_id]
        original = {"title": title, "summary": summary, "category": category, "format": video_format}
        if row.creation_content != original or row.status == "deleted":
            continue
        changed = []
        for field in ("draft_content", "published_content"):
            content = getattr(row, field)
            if not content:
                continue
            neutral = dict(content)
            for key in ("summary",):
                if neutral.get(key) == original[key]:
                    neutral[key] = ""
            if neutral != content:
                setattr(row, field, neutral)
                changed.append(field)
        if changed:
            row.revision += 1
            row.save(using=alias, update_fields=[*changed, "revision"])
            Event.objects.using(alias).create(tenant_id=row.tenant_id, video_id=row.pk, action="neutralize",
                                             video_revision=row.revision, detail={"fields": changed})


class Migration(migrations.Migration):
    dependencies = [("landing_public", "0012_remove_publicvideo_public_video_tenant_youtube_and_more")]
    operations = [migrations.RunPython(neutralize_launch_copy, migrations.RunPython.noop)]
