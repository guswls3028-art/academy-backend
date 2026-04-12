# PATH: apps/domains/students/views/password_views.py

import logging

from django.db.models import Q
from django.contrib.auth import get_user_model

from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.core.permissions import TenantResolved
from apps.api.common.throttles import SmsEndpointThrottle
from apps.core.models import TenantMembership
from apps.core.models.user import user_display_username

from academy.adapters.db.django import repositories_students as student_repo
from ..models import Student

logger = logging.getLogger(__name__)


def _pw_reset_cache_key(tenant_id, phone: str) -> str:
    return f"pw_reset:{tenant_id}:{phone}"


class StudentPasswordFindRequestView(APIView):
    """POST: name, phone → 학생 조회 후 6자리 인증번호 알림톡 발송, 캐시 저장."""
    permission_classes = [AllowAny, TenantResolved]
    throttle_classes = [SmsEndpointThrottle]

    def get_authenticators(self):
        return []  # 비로그인 요청 허용, 만료 JWT 시 401 방지

    def post(self, request):
        from django.core.cache import cache
        from apps.support.messaging.selectors import get_auto_send_config
        from apps.support.messaging.policy import MessagingPolicyError, is_messaging_disabled

        name = (request.data.get("name") or "").strip()
        phone = (request.data.get("phone") or "").replace(" ", "").replace("-", "").replace(".", "")
        if not name or not phone or len(phone) != 11 or not phone.startswith("010"):
            return Response(
                {"detail": "이름과 학생 전화번호(010XXXXXXXX 11자리)를 입력해 주세요."},
                status=400,
            )
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant를 확인할 수 없습니다."}, status=400)

        student = Student.objects.filter(
            tenant=tenant,
            deleted_at__isnull=True,
            name=name,
        ).filter(
            Q(phone=phone) | Q(parent_phone=phone)
        ).select_related("user").first()
        if not student or not getattr(student, "user", None):
            return Response(
                {"detail": "해당 이름과 전화번호로 등록된 학생이 없습니다."},
                status=404,
            )
        import secrets
        code = "".join([str(secrets.randbelow(10)) for _ in range(6)])
        key = _pw_reset_cache_key(tenant.id, phone)
        cache.set(key, {"user_id": student.user_id, "code": code}, timeout=600)

        # 알림톡 발송 (AutoSendConfig 템플릿 사용)
        if is_messaging_disabled(tenant.id):
            return Response(
                {"message": "인증번호가 발송되었습니다. (테스트 테넌트에서는 실제 발송이 생략됩니다.)"},
                status=200,
            )

        # 오너 테넌트의 승인된 알림톡 템플릿으로 발송 (모든 테넌트 공통, SMS fallback 없음)
        # password_find_otp 전용 템플릿이 PENDING이면 registration_approved_student로 fallback
        from apps.support.messaging.policy import send_alimtalk_via_owner
        from django.conf import settings as _settings
        site_url = getattr(_settings, "SITE_URL", "") or "https://hakwonplus.com"
        ok = send_alimtalk_via_owner(
            trigger="password_find_otp",
            to=phone,
            replacements={
                "인증번호": code,
                # fallback 시 registration_approved_student 플레이스홀더 매핑
                "학생이름": student.name or "",
                "학생아이디": "인증번호 안내",
                "학생비밀번호": code,
                "사이트링크": site_url,
                "비밀번호안내": "위 인증번호를 10분 내에 입력해 주세요.",
            },
        )

        if not ok:
            return Response(
                {"detail": "인증번호 발송에 실패했습니다. 잠시 후 다시 시도해 주세요."},
                status=503,
            )
        return Response({"message": "인증번호가 발송되었습니다."}, status=200)


class StudentPasswordFindVerifyView(APIView):
    """POST: phone, code, new_password → 인증번호 확인 후 비밀번호 변경."""
    permission_classes = [AllowAny, TenantResolved]

    def get_authenticators(self):
        return []  # 비로그인 요청 허용, 만료 JWT 시 401 방지

    def post(self, request):
        from django.core.cache import cache
        phone = (request.data.get("phone") or "").replace(" ", "").replace("-", "").replace(".", "")
        code = (request.data.get("code") or "").strip()
        new_password = (request.data.get("new_password") or "").strip()
        if not phone or len(phone) != 11 or not code or len(code) != 6 or len(new_password) < 4:
            return Response(
                {"detail": "전화번호, 6자리 인증번호, 새 비밀번호(4자 이상)를 입력해 주세요."},
                status=400,
            )
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant를 확인할 수 없습니다."}, status=400)
        key = _pw_reset_cache_key(tenant.id, phone)
        payload = cache.get(key)
        if not payload or payload.get("code") != code:
            return Response({"detail": "인증번호가 일치하지 않거나 만료되었습니다."}, status=400)
        user_id = payload.get("user_id")
        if not user_id:
            return Response({"detail": "잘못된 요청입니다."}, status=400)
        User = get_user_model()
        user = User.objects.filter(pk=user_id, tenant=tenant).first()
        if not user:
            return Response({"detail": "사용자를 찾을 수 없습니다."}, status=404)
        user.set_password(new_password)
        user.save(update_fields=["password"])
        cache.delete(key)
        return Response({"message": "비밀번호가 변경되었습니다."}, status=200)


def _normalize_phone_for_reset(value):
    """전화번호 정규화 (하이픈 제거, 11자리)."""
    s = (value or "").replace(" ", "").replace("-", "").replace(".", "").strip()
    return s if len(s) == 11 and s.startswith("010") else ""


def _generate_temp_password(length=10):
    """임시 비밀번호 생성 (영문+숫자)."""
    import secrets
    import string
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


class StudentPasswordResetSendView(APIView):
    """
    POST: 학생 또는 학부모 비밀번호 재설정 — 이름+학생번호 또는 이름+학부모번호로 조회 후
    임시 비밀번호 생성·저장하고 알림톡(SMS)으로 발송.
    """
    permission_classes = [AllowAny, TenantResolved]
    throttle_classes = [SmsEndpointThrottle]

    def get_authenticators(self):
        """AllowAny이지만 JWT가 있으면 파싱 — staff 판별용 (temp_password/skip_notify)."""
        from rest_framework_simplejwt.authentication import JWTAuthentication
        return [JWTAuthentication()]

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant를 확인할 수 없습니다."}, status=400)

        target = (request.data.get("target") or "").strip().lower()
        student_name = (request.data.get("student_name") or "").strip()
        student_ps_number = (request.data.get("student_ps_number") or "").strip()
        parent_phone = _normalize_phone_for_reset(request.data.get("parent_phone") or "")

        if target not in ("student", "parent"):
            return Response({"detail": "대상을 선택해 주세요. (학생 / 학부모)"}, status=400)
        if not student_name:
            return Response({"detail": "학생 이름을 입력해 주세요."}, status=400)

        if target == "student":
            student_phone = _normalize_phone_for_reset(request.data.get("student_phone") or "")
            if not student_ps_number and not student_phone:
                return Response({"detail": "학생 전화번호를 입력해 주세요."}, status=400)
            # 전화번호 우선, 없으면 PS번호로 조회
            if student_phone:
                student = (
                    Student.objects.filter(
                        tenant=tenant,
                        deleted_at__isnull=True,
                        name__iexact=student_name,
                    )
                    .filter(Q(phone=student_phone) | Q(parent_phone=student_phone))
                    .select_related("user")
                    .first()
                )
            else:
                student = (
                    student_repo.student_filter_tenant_ps_number(tenant, student_ps_number)
                    .filter(name__iexact=student_name)
                    .select_related("user")
                    .first()
                )
            if not student or not getattr(student, "user", None):
                return Response(
                    {"detail": "해당 이름과 전화번호로 등록된 학생이 없습니다."},
                    status=404,
                )
            send_to = (student.phone or "").replace(" ", "").replace("-", "").strip()
            if not send_to or len(send_to) != 11:
                send_to = (student.parent_phone or "").replace(" ", "").replace("-", "").strip()
            if not send_to or len(send_to) != 11:
                return Response(
                    {"detail": "등록된 휴대번호가 없어 발송할 수 없습니다. 학원에 문의해 주세요."},
                    status=400,
                )
            user = student.user
            display_name = student.name
            display_username = student.ps_number or user_display_username(user)
        else:
            if not parent_phone:
                return Response({"detail": "학부모 전화번호를 010 11자리로 입력해 주세요."}, status=400)
            student = student_repo.student_filter_tenant_name_parent_phone_active(
                tenant, student_name, parent_phone
            )
            if not student:
                return Response(
                    {"detail": "해당 학생 이름과 학부모 전화번호로 등록된 정보가 없습니다."},
                    status=404,
                )
            # Ensure parent account exists (may not exist if student was created before parent auto-creation was added)
            from apps.domains.parents.services import ensure_parent_for_student
            ensure_parent_for_student(
                tenant=tenant,
                parent_phone=parent_phone,
                student_name=student.name,
            )
            from apps.domains.parents.models import Parent
            parent = Parent.objects.filter(tenant=tenant, phone=parent_phone).first()
            if not parent or not getattr(parent, "user", None):
                return Response(
                    {"detail": "학부모 계정을 찾을 수 없습니다. 학원에 문의해 주세요."},
                    status=404,
                )
            user = parent.user
            send_to = parent_phone
            display_name = parent.name or f"{student.name} 학부모"
            display_username = parent_phone

        # ✅ 보안: 클라이언트 지정 비밀번호는 인증된 관리자/교사만 허용
        # (이 엔드포인트는 AllowAny이므로 비인증 요청에서는 서버 생성만 사용)
        client_temp_password = (request.data.get("temp_password") or "").strip()
        is_staff_request = (
            getattr(request, "user", None)
            and request.user.is_authenticated
            and hasattr(request, "tenant")
            and TenantMembership.objects.filter(
                user=request.user, tenant=request.tenant,
                role__in=["owner", "admin", "teacher", "staff"],
                is_active=True,
            ).exists()
        )
        temp_password = (
            client_temp_password
            if client_temp_password and is_staff_request
            else _generate_temp_password()
        )
        old_password_hash = user.password  # 발송 실패 시 롤백용
        user.set_password(temp_password)
        user.save(update_fields=["password"])

        # skip_notify: 비밀번호만 변경, 알림톡 발송 안 함 (관리자 전용)
        skip_notify = bool(request.data.get("skip_notify", False)) and is_staff_request
        if skip_notify:
            return Response({"message": "비밀번호가 변경되었습니다. (알림톡 미발송)"}, status=200)

        # 알림톡 발송
        from apps.support.messaging.selectors import get_auto_send_config
        from apps.support.messaging.policy import MessagingPolicyError, is_messaging_disabled

        if is_messaging_disabled(tenant.id):
            return Response(
                {"message": "임시 비밀번호가 발송되었습니다. (테스트 환경에서는 실제 발송이 생략됩니다.)"},
                status=200,
            )

        notice = "로그인 후 설정에서 비밀번호를 변경하실 수 있습니다."
        trigger = "password_reset_student" if target == "student" else "password_reset_parent"
        config = get_auto_send_config(tenant.id, trigger)

        if target == "student":
            fallback_text = (
                f"[학원] 비밀번호 찾기\n"
                f"이름: {display_name}\n"
                f"아이디: {display_username}\n"
                f"임시 비밀번호: {temp_password}\n"
                f"{notice}"
            )
        else:
            fallback_text = (
                f"[학원] 비밀번호 찾기\n"
                f"이름: {display_name}\n"
                f"아이디(학부모 전화번호): {display_username}\n"
                f"임시 비밀번호: {temp_password}\n"
                f"{notice}"
            )

        # 오너 테넌트의 승인된 알림톡 템플릿으로 발송 (모든 테넌트 공통, SMS fallback 없음)
        from apps.support.messaging.policy import send_alimtalk_via_owner
        from django.conf import settings as _settings
        site_url = getattr(_settings, "SITE_URL", "") or "https://hakwonplus.com"
        trigger = "password_reset_student" if target == "student" else "password_reset_parent"
        replacements = {
            "학생이름": display_name or "",
            "학생아이디": display_username or "",
            "학생비밀번호": temp_password,
            "아이디": display_username or "",
            "임시비밀번호": temp_password,
            "비밀번호안내": notice,
            "사이트링크": site_url,
        }
        if target == "parent":
            replacements["학부모아이디"] = display_username or ""
            replacements["학부모비밀번호"] = temp_password
        ok = send_alimtalk_via_owner(trigger=trigger, to=send_to, replacements=replacements)

        if not ok:
            # 발송 실패 시 비밀번호 롤백 — 비밀번호는 바뀌었는데 알림 못 받는 상황 방지
            user.password = old_password_hash
            user.save(update_fields=["password"])
            return Response(
                {"detail": "임시 비밀번호 발송에 실패했습니다. 잠시 후 다시 시도해 주세요."},
                status=503,
            )
        return Response({"message": "임시 비밀번호가 발송되었습니다. 알림톡을 확인해 주세요."}, status=200)
