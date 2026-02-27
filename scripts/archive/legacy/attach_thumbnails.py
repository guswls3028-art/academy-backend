from apps.support.video.models import Video
from pathlib import Path
from django.conf import settings
from django.db.models import Q

PROJECT_ROOT = settings.BASE_DIR.parent

qs = Video.objects.filter(
    status="READY"
).filter(
    Q(thumbnail__isnull=True) | Q(thumbnail="")
)

print("대상 수:", qs.count())

for v in qs:
    thumb_abs = (
        PROJECT_ROOT
        / "storage"
        / "media"
        / "hls"
        / "videos"
        / str(v.id)
        / "thumbnail.jpg"
    )

    if not thumb_abs.exists():
        print("❌ 썸네일 없음:", v.id, thumb_abs)
        continue

    v.thumbnail = str(thumb_abs.relative_to(settings.MEDIA_ROOT))
    v.save(update_fields=["thumbnail"])
    print("✅ thumbnail attached:", v.id)
