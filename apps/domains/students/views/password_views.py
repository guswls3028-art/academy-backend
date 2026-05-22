# PATH: apps/domains/students/views/password_views.py

from django.db.models import Q
from django.contrib.auth import get_user_model

from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from apps.core.permissions import TenantResolved
from apps.core.parsing import parse_bool
from apps.api.common.throttles import SmsEndpointThrottle
from apps.core.models import TenantMembership
from apps.core.models.user import user_display_username
from apps.core.services.password import generate_temp_password

from academy.adapters.db.django import repositories_students as student_repo
from ..models import Student


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
        from apps.domains.messaging.policy import is_messaging_disabled

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

        candidates = Student.objects.filter(
            tenant=tenant,
            deleted_at__isnull=True,
            name=name,
        ).filter(
            Q(phone=phone) | Q(parent_phone=phone)
        ).select_related("user").order_by("id")

        matches = list(candidates[:2])
        if len(matches) != 1:
            # 계정 존재 여부 노출 방지 + 다건 매칭(동명이인/공유번호) 안전 차단
            return Response({"message": "인증번호가 발송되었습니다."}, status=200)
        student = matches[0]
        if not student or not getattr(student, "user", None):
            return Response({"message": "인증번호가 발송되었습니다."}, status=200)
        import secrets
        code = "".join([str(secrets.randbelow(10)) for _ in range(6)])
        key = _pw_reset_cache_key(tenant.id, phone)
        cache.set(key, {"user_id": student.user_id, "code": code}, timeout=600)
        cache.delete(f"{key}:fail")

        # 알림톡 발송 (AutoSendConfig 템플릿 사용)
        if is_messaging_disabled(tenant.id):
            return Response(
                {"message": "인증번호가 발송되었습니다. (테스트 테넌트에서는 실제 발송이 생략됩니다.)"},
                status=200,
            )

        # 오너 테넌트의 승인된 알림톡 템플릿으로 발송 (모든 테넌트 공통, SMS fallback 없음)
        # password_find_otp 전용 템플릿이 PENDING이면 registration_approved_student로 fallback
        from apps.domains.messaging.policy import send_alimtalk_via_owner
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
            cache.delete(key)
            cache.delete(f"{key}:fail")
            return Response(
                {"detail": "인증번호 발송에 실패했습니다. 잠시 후 다시 시도해 주세요."},
                status=503,
            )
        return Response({"message": "인증번호가 발송되었습니다."}, status=200)


class StudentPasswordFindVerifyView(APIView):
    """POST: phone, code, new_password → 인증번호 확인 후 비밀번호 변경.

    OTP brute-force 방어: throttle + 검증 실패 누적 5회 시 캐시 키 invalidate.
    """
    permission_classes = [AllowAny, TenantResolved]
    throttle_classes = [SmsEndpointThrottle]

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
            # 실패 누적 카운트 — 5회 초과 시 OTP 키 무효화 (brute-force 차단).
            fail_key = f"{key}:fail"
            try:
                fails = cache.incr(fail_key)
            except ValueError:
                cache.set(fail_key, 1, timeout=600)
                fails = 1
            if fails >= 5:
                cache.delete(key)
                cache.delete(fail_key)
                return Response(
                    {"detail": "인증 실패 횟수를 초과했습니다. 인증번호를 다시 발급해 주세요."},
                    status=400,
                )
            return Response({"detail": "인증번호가 일치하지 않거나 만료되었습니다."}, status=400)
        user_id = payload.get("user_id")
        if not user_id:
            return Response({"detail": "잘못된 요청입니다."}, status=400)
        User = get_user_model()
        user = User.objects.filter(pk=user_id, tenant=tenant).first()
        if not user:
            return Response({"detail": "사용자를 찾을 수 없습니다."}, status=404)
        from apps.core.services.password import change_password
        change_password(user, new_password)
        cache.delete(key)
        cache.delete(f"{key}:fail")
        return Response({"message": "비밀번호가 변경되었습니다."}, status=200)


def _normalize_phone_for_reset(value):
    """전화번호 정규화 (하이픈 제거, 11자리)."""
    s = (value or "").replace(" ", "").replace("-", "").replace(".", "").strip()
    return s if len(s) == 11 and s.startswith("010") else ""


def _is_staff_password_reset_request(request) -> bool:
    user = getattr(request, "user", None)
    tenant = getattr(request, "tenant", None)
    return bool(
        user
        and user.is_authenticated
        and tenant
        and TenantMembership.objects.filter(
            user=user,
            tenant=tenant,
            role__in=["owner", "admin", "teacher", "staff"],
            is_active=True,
        ).exists()
    )


class StudentPasswordResetSendView(APIView):
    """
    POST: 학생 또는 학부모 비밀번호 재설정 — 이름+학생번호 또는 이름+학부모번호로 조회 후
    임시 비밀번호 생성·저장하고 알림톡(SMS)으로 발송.
    """
    permission_classes = [AllowAny, TenantResolved]
    throttle_classes = [SmsEndpointThrottle]

    def get_authenticators(self):
        """AllowAny이지만 JWT가 있으면 파싱 — staff 판별용 (temp_password/skip_notify)."""
        from apps.core.authentication import TokenVersionJWTAuthentication

        return [TokenVersionJWTAuthentication()]

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

        try:
            skip_notify_requested = parse_bool(
                request.data.get("skip_notify", False),
                field_name="skip_notify",
            )
        except ValidationError as e:
            return Response(e.detail, status=400)

        is_staff_request = _is_staff_password_reset_request(request)

        if not is_staff_request:
            from apps.domains.students.services.account_recovery import (
                AccountRecoveryDeliveryError,
                AccountRecoveryValidationError,
                resolve_recovery_account,
                send_password_recovery,
                validate_recovery_payload,
            )

            verified_phone = parent_phone
            if target == "student":
                verified_phone = _normalize_phone_for_reset(request.data.get("student_phone") or "") or parent_phone

            try:
                _, recovery_target, name, phone = validate_recovery_payload(
                    mode="password",
                    target=target,
                    name=student_name,
                    phone=verified_phone,
                )
            except AccountRecoveryValidationError as e:
                return Response({"detail": e.detail}, status=400)

            account = resolve_recovery_account(
                tenant=tenant,
                target=recovery_target,
                name=name,
                phone=phone,
            )
            message = "입력한 정보가 등록되어 있다면 해당 번호로 임시 비밀번호가 발송됩니다."
            if account is None:
                return Response({"message": message}, status=200)

            try:
                send_password_recovery(account)
            except AccountRecoveryValidationError as e:
                return Response({"detail": e.detail}, status=400)
            except AccountRecoveryDeliveryError as e:
                return Response({"detail": e.detail}, status=503)
            return Response({"message": message}, status=200)

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
        temp_password = (
            client_temp_password
            if client_temp_password and is_staff_request
            else generate_temp_password()
        )
        # 비밀번호 정책: 최소 4자 (PASSWORD_POLICY_4CHAR). staff가 더 짧게 입력해도 강제 적용.
        if len(temp_password) < 4:
            return Response(
                {"detail": "임시 비밀번호는 최소 4자 이상이어야 합니다."},
                status=400,
            )

        # skip_notify: 비밀번호만 변경, 알림톡 발송 안 함 (관리자 전용)
        skip_notify = skip_notify_requested and is_staff_request

        old_password_hash = user.password  # 발송 실패 시 롤백용
        old_must_change_password = bool(getattr(user, "must_change_password", False))
        from apps.core.services.password import force_reset_password
        force_reset_password(user, temp_password)

        if skip_notify:
            return Response({"message": "비밀번호가 변경되었습니다. (알림톡 미발송)"}, status=200)

        # 알림톡 발송
        from apps.domains.messaging.policy import is_messaging_disabled

        if is_messaging_disabled(tenant.id):
            return Response(
                {"message": "임시 비밀번호가 발송되었습니다. (테스트 환경에서는 실제 발송이 생략됩니다.)"},
                status=200,
            )

        notice = "로그인 후 설정에서 비밀번호를 변경하실 수 있습니다."
        trigger = "password_reset_student" if target == "student" else "password_reset_parent"

        # 오너 테넌트의 승인된 알림톡 템플릿으로 발송 (모든 테넌트 공통, SMS fallback 없음)
        from apps.domains.messaging.policy import send_alimtalk_via_owner
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
            from apps.core.services.password import rollback_password
            rollback_password(
                user,
                old_password_hash,
                must_change_password=old_must_change_password,
            )
            return Response(
                {"detail": "임시 비밀번호 발송에 실패했습니다. 잠시 후 다시 시도해 주세요."},
                status=503,
            )
        return Response({"message": "임시 비밀번호가 발송되었습니다. 알림톡을 확인해 주세요."}, status=200)
