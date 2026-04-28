# PATH: apps/domains/students/views/registration_views.py

import logging
import traceback

from django.db import transaction, IntegrityError
from django.db.models import Q
from django.conf import settings
from django.contrib.auth import get_user_model

from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny

from apps.core.permissions import TenantResolvedAndStaff, TenantResolved
from apps.api.common.throttles import SmsEndpointThrottle, SignupCheckThrottle
from apps.core.models import TenantMembership

from apps.domains.parents.services import ensure_parent_for_student, PARENT_DEFAULT_PASSWORD
from apps.support.messaging.services import get_tenant_site_url, send_registration_approved_messages

from academy.adapters.db.django import repositories_students as student_repo
from ..models import Student, StudentRegistrationRequest
from ..serializers import (
    _generate_unique_ps_number,
    StudentDetailSerializer,
    RegistrationRequestCreateSerializer,
    RegistrationRequestListSerializer,
)
from .student_views import StudentListPagination

logger = logging.getLogger(__name__)


# ======================================================
# 학생 가입 신청 (로그인 전 회원가입 → 선생 승인)
# ======================================================


def _approve_registration_request(request, reg):
    """
    가입 신청 1건 승인 처리. 성공 시 None 반환, 실패 시 Response 반환.
    호출 후 reg.student 가 설정됨.
    """
    from apps.core.models.user import user_internal_username

    tenant = request.tenant
    parent_fixed_password = PARENT_DEFAULT_PASSWORD
    name = reg.name
    parent_phone = reg.parent_phone
    phone = reg.phone

    # SSOT: ps_number = 로그인 아이디 = 표시 아이디 (하나의 값)
    # 학생이 요청한 아이디가 있으면 그것을 ps_number로, 없으면 랜덤
    requested_id = (reg.username or "").strip()
    if requested_id:
        internal = user_internal_username(tenant, requested_id)
        # 테넌트 내 중복 검사 (User.username + Student.ps_number)
        if get_user_model().objects.filter(username=internal).exists():
            requested_id = ""
        elif Student.objects.filter(tenant=tenant, ps_number=requested_id, deleted_at__isnull=True).exists():
            requested_id = ""
    if not requested_id:
        try:
            requested_id = _generate_unique_ps_number(tenant=tenant)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
    ps_number = requested_id  # ps_number = 로그인 아이디 = 하나의 값

    if phone and len(str(phone)) >= 8:
        omr_code = str(phone)[-8:]
    elif parent_phone and len(parent_phone) >= 8:
        omr_code = parent_phone[-8:]
    else:
        omr_code = (parent_phone or "00000000")[-8:]

    try:
        with transaction.atomic():
            # Row lock to prevent double-approve race condition
            reg = StudentRegistrationRequest.objects.select_for_update().get(pk=reg.pk)
            if reg.status != StudentRegistrationRequest.PENDING:
                return Response({"detail": "이미 처리된 신청입니다."}, status=400)

            parent = None
            if parent_phone:
                parent = ensure_parent_for_student(
                    tenant=tenant,
                    parent_phone=parent_phone,
                    student_name=name,
                )
            User = get_user_model()
            user = student_repo.user_create_user(
                username=ps_number,
                tenant=tenant,
                phone=phone or "",
                name=name,
            )
            # 가입 시 입력한 비밀번호(해시) 직접 이전 — 임시 비번 생성하지 않음
            user.password = reg.initial_password
            user.save()

            student = student_repo.student_create(
                tenant=tenant,
                user=user,
                parent=parent,
                name=name,
                parent_phone=parent_phone,
                phone=phone,
                ps_number=ps_number,
                omr_code=omr_code,
                uses_identifier=not (phone and phone.strip()),
                school_type=reg.school_type,
                elementary_school=reg.elementary_school or None,
                high_school=reg.high_school or None,
                middle_school=reg.middle_school or None,
                high_school_class=reg.high_school_class or None,
                major=reg.major or None,
                grade=reg.grade,
                gender=reg.gender or None,
                memo=reg.memo or None,
                address=reg.address or None,
                origin_middle_school=reg.origin_middle_school or None,
            )
            TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
            reg.status = StudentRegistrationRequest.APPROVED
            reg.student = student
            reg.save(update_fields=["status", "student", "updated_at"])

        # 알림톡에 실제 비밀번호 전달 (원문 보관 필드 사용)
        actual_password = (reg.initial_password_plain or "").strip()
        send_registration_approved_messages(
            tenant_id=tenant.id,
            site_url=get_tenant_site_url(request.tenant) or "",
            student_name=name,
            student_phone=(phone or "") if phone else "",
            student_id=ps_number,
            student_password=actual_password if actual_password else "비밀번호를 변경해 주세요",
            parent_phone=parent_phone or "",
            parent_password=parent_fixed_password,
        )
        # 원문 비밀번호 즉시 삭제 (보안)
        if reg.initial_password_plain:
            reg.initial_password_plain = ""
            reg.save(update_fields=["initial_password_plain"])
        return None
    except Exception as e:
        logger.exception("_approve_registration_request error: %s", e)
        return Response(
            {"detail": str(e) if settings.DEBUG else "승인 처리 중 오류가 발생했습니다."},
            status=500,
        )


class RegistrationRequestViewSet(ModelViewSet):
    """
    - create: AllowAny + TenantResolved (학생이 로그인 페이지에서 신청)
    - list / retrieve / approve: TenantResolvedAndStaff (선생이 가입신청 목록/승인)
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = RegistrationRequestListSerializer
    pagination_class = StudentListPagination

    def get_queryset(self):
        return StudentRegistrationRequest.objects.filter(
            tenant=self.request.tenant
        ).select_related("tenant", "student").order_by("-created_at")

    def filter_queryset(self, queryset):
        action = getattr(self, "action", None)
        if action == "list":
            status = self.request.query_params.get("status")
            if status in (StudentRegistrationRequest.PENDING, StudentRegistrationRequest.APPROVED, StudentRegistrationRequest.REJECTED):
                queryset = queryset.filter(status=status)
        return queryset

    def get_authenticators(self):
        # create/check_duplicate는 비로그인 요청 허용 → JWT 검사 생략 (만료 토큰 시 401 방지)
        if getattr(self, "action", None) in ("create", "check_duplicate"):
            return []
        return super().get_authenticators()

    def get_throttles(self):
        if getattr(self, "action", None) == "check_duplicate":
            return [SignupCheckThrottle()]
        if getattr(self, "action", None) == "create":
            return [SmsEndpointThrottle()]
        return super().get_throttles()

    def get_permissions(self):
        if getattr(self, "action", None) in ("create", "check_duplicate"):
            return [AllowAny(), TenantResolved()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    def get_serializer_class(self):
        if getattr(self, "action", None) == "create":
            return RegistrationRequestCreateSerializer
        return RegistrationRequestListSerializer

    @action(detail=False, methods=["post"], url_path="check_duplicate")
    def check_duplicate(self, request):
        """
        회원가입 실시간 중복검사.
        POST body: { "username": "abc", "phone": "01012345678" }
        둘 다 선택적 — 입력된 필드만 검사.
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "테넌트를 확인할 수 없습니다."}, status=403)

        username = (request.data.get("username") or "").strip()
        phone = (request.data.get("phone") or "").strip().replace("-", "")

        result = {}

        if username:
            # 1) 이미 등록된 학생 (활성). LIKE 와일드카드 회피 위해 정확 매칭.
            from apps.core.models.user import user_internal_username
            internal = user_internal_username(tenant, username)
            exists = Student.objects.filter(
                tenant=tenant,
                deleted_at__isnull=True,
                user__username=internal,
            ).exists()
            if exists:
                result["username"] = {"available": False, "reason": "이미 사용 중인 아이디입니다."}
            else:
                # 2) 승인 대기 중인 신청
                pending = StudentRegistrationRequest.objects.filter(
                    tenant=tenant,
                    status=StudentRegistrationRequest.PENDING,
                    username=username,
                ).exists()
                if pending:
                    result["username"] = {"available": False, "reason": "승인 대기 중인 아이디입니다. 선생님의 승인을 기다려 주세요."}
                else:
                    result["username"] = {"available": True}

        if phone and len(phone) == 11:
            # 1) 이미 등록된 학생 (활성)
            existing = Student.objects.filter(
                tenant=tenant,
                deleted_at__isnull=True,
            ).filter(
                Q(phone=phone) | Q(parent_phone=phone)
            ).first()
            if existing:
                result["phone"] = {
                    "available": False,
                    "reason": "이미 등록된 전화번호입니다. 기존 계정으로 로그인해 주세요.",
                }
            else:
                # 2) 승인 대기 중인 신청
                pending = StudentRegistrationRequest.objects.filter(
                    tenant=tenant,
                    status=StudentRegistrationRequest.PENDING,
                ).filter(
                    Q(phone=phone) | Q(parent_phone=phone)
                ).exists()
                if pending:
                    result["phone"] = {"available": False, "reason": "해당 전화번호로 가입 신청이 승인 대기 중입니다."}
                else:
                    result["phone"] = {"available": True}

        return Response(result)

    def create(self, request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return Response(
                {"detail": "테넌트를 확인할 수 없습니다. 로그인 URL(테넌트 코드 포함)로 접속했는지 확인해 주세요."},
                status=403,
            )
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        # 중복 가입 체크: 전화번호 또는 username이 이미 활성 학생으로 존재하는 경우
        data_check = serializer.validated_data
        phone_check = (data_check.get("phone") or "").strip()
        username_check = (data_check.get("username") or "").strip()
        tenant = request.tenant

        existing_student = None
        if phone_check:
            existing_student = Student.objects.filter(
                tenant=tenant,
                deleted_at__isnull=True,
            ).filter(
                Q(phone=phone_check) | Q(parent_phone=phone_check)
            ).select_related("user").first()
        if not existing_student and username_check:
            from apps.core.models.user import user_internal_username
            internal_check = user_internal_username(tenant, username_check)
            existing_student = Student.objects.filter(
                tenant=tenant,
                deleted_at__isnull=True,
                user__username=internal_check,
            ).select_related("user").first()

        if existing_student:
            return Response(
                {
                    "code": "already_registered",
                    "detail": "이미 가입된 아이디입니다.",
                    "student_name": existing_student.name,
                    "student_phone": existing_student.phone or existing_student.parent_phone or "",
                },
                status=409,
            )

        from django.contrib.auth.hashers import make_password
        data = serializer.validated_data.copy()
        raw_password = data.pop("initial_password")
        password = make_password(raw_password)
        # grade: model은 PositiveSmallIntegerField(null=True) → int 또는 None만 허용
        raw_grade = data.get("grade")
        if raw_grade is not None and raw_grade != "":
            try:
                grade = int(raw_grade)
                if grade < 0 or grade > 32767:
                    grade = None
            except (TypeError, ValueError):
                grade = None
        else:
            grade = None

        try:
            req = StudentRegistrationRequest.objects.create(
                tenant=request.tenant,
                status=StudentRegistrationRequest.PENDING,
                initial_password=password,
                initial_password_plain=raw_password,
                name=data.get("name", ""),
                username=(data.get("username") or "").strip() or "",
                parent_phone=data.get("parent_phone", ""),
                phone=data.get("phone"),
                school_type=data.get("school_type", "HIGH"),
                elementary_school=(data.get("elementary_school") or "") or None,
                high_school=(data.get("high_school") or "") or None,
                middle_school=(data.get("middle_school") or "") or None,
                high_school_class=(data.get("high_school_class") or "") or None,
                major=(data.get("major") or "") or None,
                grade=grade,
                gender=(data.get("gender") or "").strip() or None,
                memo=(data.get("memo") or "") or None,
                address=(data.get("address") or "").strip() or None,
                origin_middle_school=(data.get("origin_middle_school") or "").strip() or None,
            )
        except IntegrityError as e:
            logger.warning("RegistrationRequest create IntegrityError: %s", e)
            payload = {"detail": "저장 중 제약 조건 오류가 발생했습니다. 입력값(이름·전화번호 등)을 확인해 주세요."}
            if settings.DEBUG:
                payload["error"] = str(e)
            return Response(payload, status=400)
        except Exception as e:
            logger.exception("RegistrationRequest create error: %s", e)
            payload = {"detail": "가입 신청 저장 중 오류가 발생했습니다."}
            if settings.DEBUG:
                payload["error"] = str(e)
                payload["traceback"] = traceback.format_exc()
            return Response(payload, status=500)

        # 자동 승인 설정 시 즉시 승인 처리
        try:
            if getattr(request.tenant, "student_registration_auto_approve", False):
                err = _approve_registration_request(request, req)
                if err is not None:
                    return err
                return Response(
                    StudentDetailSerializer(req.student, context={"request": request}).data,
                    status=200,
                )
        except Exception:
            pass

        try:
            out = RegistrationRequestListSerializer(req, context={"request": request})
            return Response(out.data, status=201)
        except Exception as e:
            logger.exception("RegistrationRequest response serialize error: %s", e)
            payload = {"detail": "가입 신청이 저장되었으나 응답 생성 중 오류가 발생했습니다."}
            if settings.DEBUG:
                payload["error"] = str(e)
            return Response(payload, status=500)

    @action(detail=False, methods=["post"], url_path="bulk_approve")
    def bulk_approve(self, request):
        """
        선택한 가입 신청 일괄 승인.
        POST body: { "ids": [1, 2, 3, ...] }
        응답: { "approved": int, "failed": [ {"id": int, "detail": str}, ... ] }
        """
        ids = request.data.get("ids") or []
        if not isinstance(ids, (list, tuple)):
            return Response({"detail": "ids는 배열이어야 합니다."}, status=400)
        ids = [int(x) for x in ids if isinstance(x, (int, str)) and str(x).isdigit()]
        if not ids:
            return Response({"detail": "승인할 ID가 없습니다."}, status=400)

        tenant = request.tenant
        approved_count = 0
        failed = []

        for rid in ids:
            reg = StudentRegistrationRequest.objects.filter(
                tenant=tenant,
                id=rid,
            ).first()
            if not reg:
                failed.append({"id": rid, "detail": "신청을 찾을 수 없습니다."})
                continue
            if reg.status != StudentRegistrationRequest.PENDING:
                failed.append({"id": rid, "detail": "이미 처리된 신청입니다."})
                continue
            err_response = _approve_registration_request(request, reg)
            if err_response is not None:
                failed.append({"id": rid, "detail": err_response.data.get("detail", "승인 실패")})
            else:
                approved_count += 1

        return Response({"approved": approved_count, "failed": failed}, status=200)

    @action(detail=False, methods=["get", "patch"], url_path="settings")
    def registration_settings(self, request):
        """
        가입 신청 설정 조회/수정 (자동 승인).
        GET → { "auto_approve": bool }
        PATCH body: { "auto_approve": bool } → 200 동일 형식
        """
        tenant = request.tenant
        if request.method == "GET":
            try:
                auto_approve = getattr(tenant, "student_registration_auto_approve", False)
            except Exception:
                auto_approve = False
            return Response({
                "auto_approve": bool(auto_approve),
            })
        if request.method == "PATCH":
            auto_approve = request.data.get("auto_approve")
            if auto_approve is not None:
                try:
                    tenant.student_registration_auto_approve = bool(auto_approve)
                    tenant.save(update_fields=["student_registration_auto_approve"])
                except Exception:
                    pass
            try:
                auto_approve = getattr(tenant, "student_registration_auto_approve", False)
            except Exception:
                auto_approve = False
            return Response({
                "auto_approve": bool(auto_approve),
            })
        return Response({"detail": "Method not allowed."}, status=405)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """승인 시 Student + User + TenantMembership 생성 후 status=approved."""
        reg = self.get_object()
        if reg.status != StudentRegistrationRequest.PENDING:
            return Response(
                {"detail": "이미 처리된 신청입니다."},
                status=400,
            )
        err = _approve_registration_request(request, reg)
        if err is not None:
            return err
        out = StudentDetailSerializer(reg.student, context={"request": request})
        return Response(out.data, status=200)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """가입 신청 거절 → status=rejected."""
        reg = self.get_object()
        if reg.status != StudentRegistrationRequest.PENDING:
            return Response(
                {"detail": "이미 처리된 신청입니다."},
                status=400,
            )
        with transaction.atomic():
            # Row lock to prevent approve/reject race condition
            reg = StudentRegistrationRequest.objects.select_for_update().get(pk=reg.pk)
            if reg.status != StudentRegistrationRequest.PENDING:
                return Response(
                    {"detail": "이미 처리된 신청입니다."},
                    status=400,
                )
            reg.status = StudentRegistrationRequest.REJECTED
            reg.save(update_fields=["status", "updated_at"])
        return Response({"status": "rejected", "id": reg.id}, status=200)

    @action(detail=False, methods=["post"], url_path="bulk_reject")
    def bulk_reject(self, request):
        """
        선택한 가입 신청 일괄 거절.
        POST body: { "ids": [1, 2, 3, ...] }
        """
        ids = request.data.get("ids") or []
        if not isinstance(ids, (list, tuple)):
            return Response({"detail": "ids는 배열이어야 합니다."}, status=400)
        ids = [int(x) for x in ids if isinstance(x, (int, str)) and str(x).isdigit()]
        if not ids:
            return Response({"detail": "거절할 ID가 없습니다."}, status=400)

        tenant = request.tenant
        with transaction.atomic():
            # Row lock to prevent approve/reject race condition
            updated = StudentRegistrationRequest.objects.select_for_update().filter(
                tenant=tenant,
                id__in=ids,
                status=StudentRegistrationRequest.PENDING,
            ).update(status=StudentRegistrationRequest.REJECTED)
        return Response({"rejected": updated}, status=200)
