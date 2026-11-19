# apps/core/authentication.py

import re
import uuid

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


_teacher_write_domains = re.compile(r"^/api/v1/(students|lectures|enrollments|attendance|exams|homework|results|surveys)/")
_restricted_actions = re.compile(r"(password|support-session|rotate|delete|purge|archive|send|contact|notify|billing|payment)", re.I)
_qr_point_change = re.compile(r"^/api/v1/lectures/attendance-qr/[1-9]\d*/?$")
_dawon_counsel_reply = re.compile(r"/api/v1/community/posts/([1-9]\d{0,17})/replies/")
_dawon_video_action = re.compile(r"/api/v1/student/video/videos/([1-9]\d{0,17})/(playback|progress|review-watch)/")
_dawon_video_path = "assets/dawon-lesson-r13/master.m3u8"
_dawon_counsel_examples = {
    "오답 복습 방법": "예시 학생이 틀린 문제를 다시 풀 때 어떤 순서로 복습하면 좋을까요?",
    "시험 전 학습 계획": "예시 학생이 다음 시험까지 일주일 동안 실천할 학습 계획을 알려 주세요.",
    "수업 진도 확인": "예시 학생이 이번 주에 배운 내용과 다음 수업 준비물을 알고 싶습니다.",
}
_dawon_counsel_answers = {
    "틀린 이유를 한 줄로 적고, 다음 날 같은 문제를 풀이 없이 다시 풀어 보세요.",
    "첫 이틀은 개념을 복습하고, 이후에는 유형별 문제와 오답을 번갈아 풀어 보세요.",
    "이번 주에는 일차방정식을 배웠습니다. 다음 수업에는 오답 노트와 교재를 준비해 주세요.",
}
_student_qr_actions = {"/api/v1/student/attendance/qr/resolve/", "/api/v1/student/attendance/qr/record/"}


def _allows_dawon_workflow_write(request, role):
    from apps.domains.clinic.dawon_demo import allows_demo_clinic_write, is_demo_tenant

    tenant = getattr(request, "tenant", None)
    if not is_demo_tenant(tenant):
        return False
    if allows_demo_clinic_write(request, role):
        return True
    if request.method != "POST":
        return False

    path = request.path
    if role == "guardian" and path == "/api/v1/community/posts/":
        return (
            request.headers.get("X-Student-Id") == "2"
            and isinstance(request.data, dict)
            and request.data.get("post_type") == "counsel"
            and str(request.data.get("created_by")) == "2"
            and not request.data.get("node_ids")
            and not request.data.get("category_label")
            and isinstance(request.data.get("title"), str)
            and request.data.get("title") in _dawon_counsel_examples
            and request.data.get("content") == _dawon_counsel_examples[request.data["title"]]
        )
    reply = _dawon_counsel_reply.fullmatch(path)
    if role == "teacher" and reply:
        from apps.domains.community.models import PostEntity

        if not isinstance(request.data, dict):
            return False
        content = request.data.get("content")
        return (
            isinstance(content, str)
            and content in _dawon_counsel_answers
            and not request.data.get("parent_reply")
            and PostEntity.objects.filter(
                pk=reply[1], tenant=tenant, post_type="counsel", author_role="parent", created_by_id=2,
            ).exists()
        )

    if role not in {"student", "guardian"}:
        return False
    video = _dawon_video_action.fullmatch(path)
    if video:
        video_id = video[1]
    elif path == "/api/v1/media/playback/renew/" and isinstance(request.data, dict):
        from apps.domains.video.drm import verify_playback_token

        playback_token = request.data.get("token")
        if not isinstance(playback_token, str):
            return False
        ok, payload, _ = verify_playback_token(playback_token)
        if not ok:
            return False
        video_id = payload.get("video_id")
    else:
        return False

    from apps.domains.video.models import Video

    return Video.objects.filter(
        pk=video_id, tenant=tenant, hls_path=_dawon_video_path, status=Video.Status.READY,
    ).exists()


def _validate_dawon_demo_access(request, token):
    role = token.get("dawon_demo_role")
    if role not in {"student", "guardian", "teacher"} or token.get("dawon_demo_until", 0) <= int(timezone.now().timestamp()):
        raise AuthenticationFailed("체험 세션이 만료되었습니다. 데모 계정으로 다시 시작해 주세요.")

    path = request.path
    method = request.method
    if _restricted_actions.search(path) or (method == "PATCH" and _qr_point_change.fullmatch(path)):
        raise PermissionDenied("이 작업은 담당자 계정에서 할 수 있습니다.")
    if method in {"GET", "HEAD", "OPTIONS"}:
        return
    if role == "student" and method == "POST" and path in _student_qr_actions:
        return
    if role == "student" and method == "PUT" and path == "/api/v1/results/school-reports/me/":
        return
    if role == "teacher" and method in {"POST", "PATCH", "PUT"} and _teacher_write_domains.match(path):
        return
    if _allows_dawon_workflow_write(request, role):
        return
    raise PermissionDenied("이 작업은 담당자 계정에서 할 수 있습니다.")


class TenantAwareSessionAuthentication(SessionAuthentication):
    """Django session authentication bound to active tenant authorization."""

    def authenticate(self, request):
        authenticated = super().authenticate(request)
        if authenticated is None:
            return None
        user, auth = authenticated
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            raise AuthenticationFailed(
                "세션 요청의 학원 정보를 확인할 수 없습니다.",
                code="session_tenant_required",
            )
        from apps.core.services.tenant_access import user_has_active_tenant_access
        if not user_has_active_tenant_access(user, tenant):
            raise AuthenticationFailed(
                "이 학원에 대한 계정 권한이 만료되었습니다.",
                code="tenant_membership_inactive",
            )
        return user, auth


class TokenVersionJWTAuthentication(JWTAuthentication):
    """
    JWT 검증 시 token_version claim과 DB 값을 비교.
    비밀번호 변경 후 발급된 토큰만 유효하고, 이전 토큰은 즉시 무효화된다.

    추가: tenant_id claim과 현재 resolve된 tenant를 교차 검증.
    크로스테넌트 헤더 조작(X-Tenant-Code 변조) 방어 — 권한 단계 검증
    이전에 인증 단계에서 차단한다 (defense-in-depth).
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        claim_tv = validated_token.get("token_version")
        db_tv = getattr(user, "token_version", 0) or 0
        claim_tid = validated_token.get("tenant_id")
        if claim_tv is None or claim_tid is None:
            raise AuthenticationFailed(
                "필수 보안 정보가 없는 토큰입니다. 다시 로그인해 주세요.",
                code="token_claims_missing",
            )
        if claim_tv != db_tv:
            raise AuthenticationFailed(
                "세션이 만료되었습니다. 다시 로그인해 주세요.",
                code="token_version_mismatch",
            )

        # Required tenant_id claim cross-check.
        # 현재 tenant 컨텍스트는 tenant middleware(미들웨어 단계)에서 set 됨.
        try:
            from apps.core.tenant.context import get_current_tenant
            cur = get_current_tenant()
            if claim_tid is not None and cur is not None and int(claim_tid) != int(cur.id):
                raise AuthenticationFailed(
                    "토큰과 학원 정보가 일치하지 않습니다.",
                    code="tenant_mismatch",
                )
            access_tenant = cur
            if access_tenant is None and claim_tid is not None:
                from academy.adapters.db.django import repositories_core as core_repo
                access_tenant = core_repo.tenant_get_by_id(claim_tid)
                if access_tenant is None:
                    raise AuthenticationFailed(
                        "토큰의 학원 정보가 만료되었거나 비활성 상태입니다.",
                        code="tenant_inactive",
                    )
            if access_tenant is not None:
                from apps.core.services.tenant_access import user_has_active_tenant_access
                if not user_has_active_tenant_access(user, access_tenant):
                    raise AuthenticationFailed(
                        "이 학원에 대한 계정 권한이 만료되었습니다.",
                        code="tenant_membership_inactive",
                    )
        except AuthenticationFailed:
            raise
        except (TypeError, ValueError):
            raise AuthenticationFailed(
                "토큰의 학원 정보가 올바르지 않습니다.",
                code="tenant_mismatch",
            )
        return user

    def authenticate(self, request):
        authenticated = super().authenticate(request)
        if authenticated is None:
            return None
        user, token = authenticated
        if token.get("dawon_demo_role"):
            _validate_dawon_demo_access(request, token)
        if not token.get("support_preview"):
            return authenticated

        from apps.core.services.ops_audit import record_audit
        from apps.core.services.tenant_access import user_has_active_staff_access
        from django.utils import timezone
        from apps.domains.students.models import StudentSupportSession

        tenant_id = token.get("tenant_id")
        student_id = token.get("support_student_id")
        operator_id = token.get("impersonated_by")
        session_id = str(token.get("support_session_id") or "").strip()
        try:
            session_uuid = uuid.UUID(session_id)
        except (TypeError, ValueError, AttributeError):
            raise AuthenticationFailed(
                "학생 지원 세션 정보가 올바르지 않습니다.",
                code="student_support_session_inactive",
            )
        support_session = (
            StudentSupportSession.objects
            .select_related("student__user", "student__tenant", "operator")
            .filter(
                id=session_uuid,
                tenant_id=tenant_id,
                student_id=student_id,
                student__tenant_id=tenant_id,
                student__user=user,
                student__deleted_at__isnull=True,
                operator_id=operator_id,
                operator__is_active=True,
                ended_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
        if support_session is None or support_session.operator is None:
            raise AuthenticationFailed(
                "학생 지원 세션 정보가 올바르지 않습니다.",
                code="student_support_session_inactive",
            )
        student = support_session.student
        operator = support_session.operator
        if not user_has_active_staff_access(
            operator,
            student.tenant,
        ):
            raise AuthenticationFailed(
                "학생 지원 담당자의 권한이 만료되었습니다.",
                code="student_support_operator_inactive",
            )
        request.student_support_operator = operator
        request.student_support_session_id = session_id
        request.student_support_session = support_session
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            record_audit(
                request,
                actor_user=operator,
                action="student_support.action",
                summary=f"학생 지원 화면 조작: {request.method} {request.path}",
                target_tenant=student.tenant,
                target_user=user,
                payload={
                    "student_id": student.id,
                    "support_session_id": session_id,
                    "method": request.method,
                    "path": request.path,
                },
            )
        return user, token
