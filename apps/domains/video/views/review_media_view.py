"""Serve protected manifests and admit short-lived reads of individual private video fragments."""

import math
import posixpath
import re
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core.signing import Signer
from django.utils.crypto import constant_time_compare
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.views import View

from academy.adapters.storage.r2_presign import create_presigned_get_url
from academy.adapters.video.review_playlist import read_review_playlist
from apps.core.r2_paths import video_hls_master_path

from apps.domains.video.models import VideoReviewWatchStream
from apps.domains.video.services.review_media import stream_access_is_current
from apps.domains.video.services.review_watch import elapsed_ms, prefetch_allows, threshold_ms


def safe_asset(path):
    return (
        bool(re.fullmatch(r"[a-zA-Z0-9._/-]+", path))
        and all(part not in ("", ".", "..") for part in path.split("/"))
        and path.endswith((".m3u8", ".ts", ".m4s", ".mp4", ".aac", ".vtt", ".key"))
    )


def rewrite_playlist(content, asset, tenant_code, signer):
    offset_ms = 0
    duration_ms = None

    def local_uri(uri, span=None):
        path = urlsplit(uri).path
        child = posixpath.normpath(posixpath.join(posixpath.dirname(asset), path))
        if (
            uri.startswith("/")
            or ":" in uri
            or not safe_asset(child)
        ):
            raise ValueError("invalid playlist path")
        params = {"tenant": tenant_code}
        if not child.endswith(".m3u8"):
            if span is None:
                raise ValueError("missing segment duration")
            params["part"] = span + "." + signer.signature(f"{child}:{span}")
        return path + "?" + urlencode(params)

    lines = []
    for line in content.splitlines():
        if line.startswith("#EXTINF:"):
            seconds = float(line[8:].split(",", 1)[0])
            if not math.isfinite(seconds) or seconds <= 0 or seconds > 2**31:
                raise ValueError("invalid segment duration")
            duration_ms = max(1, round(seconds * 1000))
        elif line.startswith("#"):
            metadata = "init" if line.startswith(("#EXT-X-MAP:", "#EXT-X-KEY:")) else None
            line = re.sub(r'URI="([^"]+)"', lambda match: 'URI="' + local_uri(match[1], metadata) + '"', line)
        elif line.strip():
            end = offset_ms + (duration_ms or 0)
            line = local_uri(line.strip(), f"{offset_ms}-{end}" if duration_ms else None)
            offset_ms = end
            duration_ms = None
        lines.append(line)
    return "\n".join(lines) + "\n"


class ReviewMediaView(View):
    def get(self, request, key, asset):
        now = timezone.now()
        stream = VideoReviewWatchStream.objects.select_related("usage__video").filter(
            key=key,
            usage__video__tenant=request.tenant,
            usage__student__tenant=request.tenant,
            usage__video__deleted_at__isnull=True,
            closed=False,
            access_until__gt=now,
        ).first()
        if stream is None:
            return HttpResponse(status=404)
        usage = stream.usage
        video = usage.video
        remaining = (
            threshold_ms(video) - usage.used_ms - elapsed_ms(stream, now)
            if video.review_watch_limit_enabled
            else 15_000
        )
        if remaining <= 0 or stream.policy_version != video.policy_version or not safe_asset(asset):
            return HttpResponse(status=403)
        if not stream_access_is_current(stream, request.tenant):
            return HttpResponse(status=403)
        canonical = video_hls_master_path(video.tenant_id, video.reused_from_id or video.pk)
        master = str(video.hls_path or canonical).removeprefix("storage/").lstrip("/")
        if posixpath.dirname(master) != posixpath.dirname(canonical):
            return HttpResponse(status=403)
        # Admission above is exact to the millisecond. A presigned storage GET starts
        # on a whole second and needs time to reach R2 even for the final admitted part.
        transfer_seconds = max(5, (remaining + 999) // 1000)
        lifetime = min(15, transfer_seconds, int((stream.access_until - now).total_seconds()))
        if lifetime <= 0:
            return HttpResponse(status=403)
        # A playlist may outlive a policy toggle. Bind its fragment paths and time
        # spans to this stream even while the teacher has disabled the limit.
        signer = Signer(salt=f"review-media:{key}")
        # An older playlist may already be buffered without signed parts while
        # the video is unrestricted. It still requires this stream's exact entitlement.
        if not asset.endswith(".m3u8") and (video.review_watch_limit_enabled or "part" in request.GET):
            span, _, mac = request.GET.get("part", "").partition(".")
            if not re.fullmatch(r"init|\d{1,16}-\d{1,16}", span) or not constant_time_compare(
                mac, signer.signature(f"{asset}:{span}"),
            ):
                return HttpResponse(status=403)
            times = None if span == "init" else tuple(map(int, span.split("-")))
            if video.review_watch_limit_enabled and not prefetch_allows(stream, now, times):
                return HttpResponse(status=429, headers={"Retry-After": "1", "Cache-Control": "no-store"})
        media_url = create_presigned_get_url(
            key=posixpath.join(posixpath.dirname(master), asset),
            bucket=settings.R2_VIDEO_BUCKET, expires_in=lifetime,
        )
        if asset.endswith(".m3u8"):
            try:
                playlist = read_review_playlist(media_url)
                if playlist is None:
                    return HttpResponse(status=502)
                content = rewrite_playlist(playlist, asset, request.tenant.code, signer)
            except ValueError:
                return HttpResponse(status=502)
            response = HttpResponse(content, content_type="application/vnd.apple.mpegurl")
        else:
            response = HttpResponseRedirect(media_url)
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response
