

from django.conf import settings
from rest_framework.response import Response
from apps.api.common.query_params import parse_query_int


from ..models import Video, AccessMode
from academy.adapters.db.django import repositories_video as video_repo
from ..serializers import VideoSerializer
from ..services.access_resolver import resolve_access_mode, get_effective_access_mode
from ..services.playback_policy import build_effective_playback_policy

# ✅ 추가: Cloudflare signed url (있으면 사용, 없으면 기존 public)
from ..cdn.cloudflare_signing import CloudflareSignedURL


class VideoPlaybackMixin:
    """
    재생 권한 / 정책 / 공통 로직

    Access Mode 정의:
    - FREE_REVIEW: 복습 모드 (제한 없음)
    - PROCTORED_CLASS: 온라인 수업 대체 (제한 적용)
    - BLOCKED: 접근 차단
    """

    def _get_student_for_user(self, request):
        return getattr(request.user, "student_profile", None)

    # ==================================================
    # 접근 제어 (Access Control)
    # ==================================================
    def _check_access(self, *, video, enrollment):
        """
        접근 가능 여부만 판단한다.
        PROCTORED_CLASS는 접근을 차단하지 않는다 (제한만 적용).
        """
        if video.status != video.Status.READY:
            return False, "video_not_ready"

        if not video_repo.session_enrollment_exists(video.session, enrollment):
            return False, "no_session_access"

        # Use SSOT access resolver
        access_mode = resolve_access_mode(video=video, enrollment=enrollment)

        if access_mode == AccessMode.BLOCKED:
            return False, "blocked"

        # FREE_REVIEW / PROCTORED_CLASS 모두 접근 허용
        return True, None

    # ==================================================
    # Permission Loader
    # ==================================================
    def _load_permission(self, *, video, enrollment):
        return video_repo.video_access_get(video, enrollment)

    # ==================================================
    # Playback Policy
    # ==================================================
    def _effective_policy(self, *, video, enrollment, perm=None):
        """
        실제 재생 제약 정책 계산
        
        Policy behavior:
        - PROCTORED_CLASS: allow_seek=False or bounded_forward, max_speed=1.0, watermark enabled
        - FREE_REVIEW: monitoring/session writes disabled; video-level playback controls preserved
        """
        access_mode = get_effective_access_mode(video=video, enrollment=enrollment)
        progress = video_repo.video_progress_get(video, enrollment)
        policy = build_effective_playback_policy(
            video=video,
            access_mode=access_mode,
            permission=perm,
            progress=progress,
        )
        policy["concurrency"] = self._concurrency_for_video(video)
        return policy

    def _concurrency_for_video(self, video):
        """테넌트 정책 우선, 미설정 시 settings fallback."""
        from ..services.playback_session import get_tenant_session_limits
        tenant = (
            getattr(getattr(getattr(video, "session", None), "lecture", None), "tenant", None)
            or getattr(video, "tenant", None)
        )
        max_sessions, max_devices = get_tenant_session_limits(tenant)
        return {"max_sessions": max_sessions, "max_devices": max_devices}

    # ==================================================
    # HLS / CDN
    # ==================================================
    def _hls_path_prefix_for_video(self, video_id: int) -> str:
        return f"/hls/videos/{video_id}/"

    def _normalize_media_path(self, path: str) -> str:
        """
        serializer와 동일 철학:
        - leading slash 제거
        - legacy storage/media normalize
        """
        p = (path or "").lstrip("/")
        if p.startswith("storage/media/"):
            return p[len("storage/") :]
        return p

    def _public_play_url(self, *, video: Video, expires_at: int, user_id: int) -> str:
        """
        ✅ 원본 계약 유지 + 최소 보강
        - video.hls_path가 있으면 그것이 single source of truth
        - 없으면 기존 기본 경로(master.m3u8)로 fallback
        - 설정값이 있으면 Cloudflare signed query를 붙여서 반환
        """
        cdn_base = settings.CDN_HLS_BASE_URL.rstrip("/")

        # 1) worker 결과가 있으면 그걸 사용
        if getattr(video, "hls_path", ""):
            rel = self._normalize_media_path(str(video.hls_path))
            path = "/" + rel if not rel.startswith("/") else rel
        else:
            # 2) 기존 fallback (경로 통일: tenants/{id}/video/hls/...)
            try:
                tenant_id = video.session.lecture.tenant_id
            except Exception:
                tenant_id = None
            if tenant_id is not None:
                from apps.core.r2_paths import video_hls_master_path
                path = "/" + video_hls_master_path(tenant_id=tenant_id, video_id=video.id)
            else:
                path = f"/media/hls/videos/{video.id}/master.m3u8"

        secret = getattr(settings, "CDN_HLS_SIGNING_SECRET", None)
        if not secret:
            return f"{cdn_base}{path}"

        signer = CloudflareSignedURL(
            secret=str(secret),
            key_id=str(getattr(settings, "CDN_HLS_SIGNING_KEY_ID", "v1")),
        )
        return signer.build_url(
            cdn_base=cdn_base,
            path=path,
            expires_at=int(expires_at),
            user_id=int(user_id),
        )

    def _set_signed_cookies(self, response: Response, *, video_id: int, expires_at: int):
        """
        Cloudflare CDN 사용으로 signed cookies 불필요
        
        Cloudflare는 쿠키 대신 query parameter 기반 signed URL 사용
        호환성을 위해 빈 함수로 유지
        """
        # Cloudflare CDN은 쿠키 대신 query parameter 사용
        # 빈 함수 유지 (호환성)
        pass

    # ==================================================
    # 학생 영상 목록 (재생 가능 여부 판단)
    # ==================================================
    def _student_list_impl(self, request):
        session_id = parse_query_int(request.query_params, "session", min_value=1)
        if session_id is None:
            return Response({"detail": "session is required"}, status=400)

        student = self._get_student_for_user(request)
        if student is None:
            return Response({"detail": "student_profile_not_linked"}, status=403)

        qs = video_repo.video_filter_by_session_ready(session_id)
        session = video_repo.session_get_by_id_with_lecture(session_id)
        lecture = session.lecture
        enrollment = video_repo.enrollment_get_by_student_lecture_active(student, lecture)

        data = []
        for v in qs:
            d = VideoSerializer(v).data

            if not enrollment:
                d["can_play"] = False
                d["reason"] = "not_enrolled"
                data.append(d)
                continue

            if not video_repo.session_enrollment_exists(session, enrollment):
                d["can_play"] = False
                d["reason"] = "no_session_access"
                data.append(d)
                continue

            ok, reason = self._check_access(video=v, enrollment=enrollment)
            d["can_play"] = bool(ok)
            d["reason"] = reason if not ok else None
            data.append(d)

        return Response(data)
