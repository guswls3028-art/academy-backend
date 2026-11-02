"""Serve playlists through the budget check. Only individual fragments redirect to the CDN."""

import posixpath
import re
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View

from academy.adapters.video.review_playlist import read_review_playlist
from apps.core.r2_paths import video_hls_master_path
from apps.domains.video.cdn.cloudflare_signing import CloudflareSignedURL
from apps.domains.video.models import VideoReviewWatchStream
from apps.domains.video.services.review_media import stream_access_is_current
from apps.domains.video.services.review_watch import admit_fragment, elapsed_ms, threshold_ms


def safe_asset(path):
    return (
        bool(re.fullmatch(r"[a-zA-Z0-9._/-]+", path))
        and all(part not in ("", ".", "..") for part in path.split("/"))
        and path.endswith((".m3u8", ".ts", ".m4s", ".mp4", ".aac", ".vtt", ".key"))
    )


def rewrite_playlist(content, asset, tenant_code):
    query = "?" + urlencode({"tenant": tenant_code})

    def local_uri(uri):
        path = urlsplit(uri).path
        if (
            uri.startswith("/")
            or ":" in uri
            or not safe_asset(posixpath.normpath(posixpath.join(posixpath.dirname(asset), path)))
        ):
            raise ValueError("invalid playlist path")
        return path + query

    lines = []
    for line in content.splitlines():
        if line.startswith("#"):
            line = re.sub(r'URI="([^"]+)"', lambda match: 'URI="' + local_uri(match[1]) + '"', line)
        elif line.strip():
            line = local_uri(line.strip())
        lines.append(line)
    return "\n".join(lines) + "\n"


class ReviewMediaView(View):
    def get(self, request, key, asset):
        now = timezone.now()
        stream = get_object_or_404(
            VideoReviewWatchStream.objects.select_related("usage__video"),
            key=key,
            usage__video__tenant=request.tenant,
            usage__student__tenant=request.tenant,
            usage__video__deleted_at__isnull=True,
            closed=False,
            access_until__gt=now,
        )
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
        canonical = video_hls_master_path(video.tenant_id, video.pk)
        master = str(video.hls_path or canonical).removeprefix("storage/").lstrip("/")
        if posixpath.dirname(master) != posixpath.dirname(canonical):
            return HttpResponse(status=403)
        secret = getattr(settings, "CDN_HLS_SIGNING_SECRET", "")
        if not secret:
            return HttpResponse(status=503)
        cdn_url = CloudflareSignedURL(secret, getattr(settings, "CDN_HLS_SIGNING_KEY_ID", "v1")).build_url(
            cdn_base=settings.CDN_HLS_BASE_URL,
            path=posixpath.join(posixpath.dirname(master), asset),
            expires_at=min(
                int(stream.access_until.timestamp()), int(now.timestamp()) + min(15, (remaining + 999) // 1000)
            ),
            user_id=stream.user_id,
        )
        if asset.endswith(".m3u8"):
            try:
                playlist = read_review_playlist(cdn_url)
                if playlist is None:
                    return HttpResponse(status=502)
                content = rewrite_playlist(playlist, asset, request.tenant.code)
            except ValueError:
                return HttpResponse(status=502)
            response = HttpResponse(content, content_type="application/vnd.apple.mpegurl")
        else:
            if video.review_watch_limit_enabled:
                admission = admit_fragment(stream, now)
                if admission != 200:
                    return HttpResponse(status=admission, headers={"Retry-After": "1", "Cache-Control": "no-store"})
            response = HttpResponseRedirect(cdn_url)
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response
