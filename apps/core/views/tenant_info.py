# PATH: apps/core/views/tenant_info.py
from django.db import transaction, connection

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from apps.core.models import Program, Tenant, TenantDomain
from apps.core.permissions import (
    TenantResolvedAndOwner,
    TenantResolvedAndStaff,
    is_platform_admin_tenant,
)
from apps.core.services.ops_audit import record_audit
from academy.adapters.db.django import repositories_core as core_repo


# --------------------------------------------------
# Maintenance Mode (global flag) — dev_app 전용
# --------------------------------------------------

class MaintenanceModeView(APIView):
    """
    GET/PATCH /api/v1/core/maintenance-mode/

    - dev_app(owner) 전용: 전체 테넌트 Program.feature_flags["maintenance_mode"] ON/OFF
    - tenant 격리를 깨지 않도록, 응답은 aggregate(count)만 반환
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def get(self, request):
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
        exempt_codes = ("hakwonplus", "9999")
        qs = Program.objects.exclude(tenant__code__in=exempt_codes)
        total = qs.count()
        enabled_count = qs.filter(feature_flags__maintenance_mode=True).count()
        enabled_for_all = bool(total and enabled_count == total)
        return Response({
            "enabled_for_all": enabled_for_all,
            "enabled_count": enabled_count,
            "total": total,
        })

    @transaction.atomic
    def patch(self, request):
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
        enabled = bool((request.data or {}).get("enabled"))

        exempt_codes = ("hakwonplus", "9999")
        program_table = Program._meta.db_table
        tenant_table = Tenant._meta.db_table

        with connection.cursor() as cursor:
            if enabled:
                # 1) exempt tenant는 항상 OFF 강제
                cursor.execute(
                    f"""
                    UPDATE {program_table} p
                    SET feature_flags = COALESCE(p.feature_flags, '{{}}'::jsonb) - 'maintenance_mode'
                    FROM {tenant_table} t
                    WHERE p.tenant_id = t.id
                      AND t.code = ANY(%s)
                    """,
                    [list(exempt_codes)],
                )

                # 2) 나머지 테넌트만 ON
                cursor.execute(
                    f"""
                    UPDATE {program_table} p
                    SET feature_flags = jsonb_set(
                        COALESCE(p.feature_flags, '{{}}'::jsonb),
                        '{{maintenance_mode}}',
                        'true'::jsonb,
                        true
                    )
                    FROM {tenant_table} t
                    WHERE p.tenant_id = t.id
                      AND NOT (t.code = ANY(%s))
                    """,
                    [list(exempt_codes)],
                )
            else:
                # OFF: 전체에서 키 제거 (exempt 포함)
                cursor.execute(
                    f"""
                    UPDATE {program_table}
                    SET feature_flags = COALESCE(feature_flags, '{{}}'::jsonb) - 'maintenance_mode'
                    """
                )

        record_audit(
            request,
            action="maintenance.toggle",
            summary=f"Maintenance mode {'ON' if enabled else 'OFF'}",
            payload={"enabled": enabled},
        )
        return self.get(request)


class TenantInfoView(APIView):
    """
    GET/PATCH /api/v1/core/tenant-info/
    현재 요청의 테넌트(소속 학원) 정보. GET=스태프 이상, PATCH=owner만.
    학생앱 "본부 진입게이트"에 노출되는 본부 전화번호 등 설정.
    """
    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAuthenticated(), TenantResolvedAndOwner()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant not resolved."}, status=403)
        academies = getattr(tenant, "academies", None) or []
        if not academies:
            # 기존 단일 학원 정보 호환
            academies = [{
                "name": (tenant.name or "").strip(),
                "phone": (getattr(tenant, "headquarters_phone", None) or "").strip(),
            }]
        return Response({
            "name": (tenant.name or "").strip(),
            "phone": (tenant.phone or "").strip(),
            "headquarters_phone": (getattr(tenant, "headquarters_phone", None) or "").strip(),
            "academies": academies,
            "og_title": (getattr(tenant, "og_title", None) or "").strip(),
            "og_description": (getattr(tenant, "og_description", None) or "").strip(),
            "og_image_url": (getattr(tenant, "og_image_url", None) or "").strip(),
        })

    def patch(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant not resolved."}, status=403)
        update_fields = []
        if "phone" in request.data:
            tenant.phone = (request.data.get("phone") or "").strip()[:50]
            update_fields.append("phone")
        if "headquarters_phone" in request.data:
            tenant.headquarters_phone = (request.data.get("headquarters_phone") or "").strip()[:50]
            update_fields.append("headquarters_phone")
        if "name" in request.data:
            tenant.name = (request.data.get("name") or "").strip()[:255]
            update_fields.append("name")
        if "academies" in request.data:
            raw = request.data.get("academies")
            if isinstance(raw, list):
                cleaned = []
                for item in raw:
                    if isinstance(item, dict):
                        name = (item.get("name") or "").strip()[:255]
                        phone = (item.get("phone") or "").strip()[:50]
                        cleaned.append({"name": name, "phone": phone})
                tenant.academies = cleaned
                update_fields.append("academies")
                # 첫 항목을 name/headquarters_phone에 동기화(기존 사용처 호환)
                if cleaned:
                    tenant.name = cleaned[0].get("name", "")[:255]
                    tenant.headquarters_phone = cleaned[0].get("phone", "")[:50]
                    if "name" not in request.data:
                        update_fields.append("name")
                    if "headquarters_phone" not in request.data:
                        update_fields.append("headquarters_phone")
        # OG 필드
        for og_field, max_len in [("og_title", 100), ("og_description", 300), ("og_image_url", 500)]:
            if og_field in request.data:
                setattr(tenant, og_field, (request.data.get(og_field) or "").strip()[:max_len])
                update_fields.append(og_field)

        if update_fields:
            tenant.save(update_fields=update_fields)
        return self.get(request)


class PublicOgMetaView(APIView):
    """
    GET /api/v1/core/og-meta/?hostname=tchul.com
    공개 API — Cloudflare Pages Function에서 호출. 인증 불필요.
    hostname으로 TenantDomain → Tenant 조회 후 OG 메타 반환.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        hostname = (request.query_params.get("hostname") or "").strip().lower()
        if not hostname:
            return Response({"title": "", "description": "", "image": ""})

        try:
            td = TenantDomain.objects.select_related("tenant").get(
                host=hostname, is_active=True,
            )
        except TenantDomain.DoesNotExist:
            return Response({"title": "", "description": "", "image": ""})

        tenant = td.tenant
        title = (tenant.og_title or "").strip() or (tenant.name or "").strip()
        description = (tenant.og_description or "").strip()
        image = (tenant.og_image_url or "").strip()

        return Response({
            "title": title,
            "description": description or f"{title} 학습 플랫폼",
            "image": image,
        }, headers={
            "Cache-Control": "public, max-age=300",
            "Access-Control-Allow-Origin": "*",
        })


class LegalConfigView(APIView):
    """
    GET  /api/v1/core/legal-config/  — 공개 API, 인증 불필요. 테넌트별 법적 고지 메타데이터.
    PATCH /api/v1/core/legal-config/ — owner 전용. 법적 고지 정보 수정.
    """
    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAuthenticated(), TenantResolvedAndOwner()]
        return [AllowAny()]

    def _get_program(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return None
        try:
            return tenant.program
        except Program.DoesNotExist:
            return None

    def get(self, request):
        program = self._get_program(request)

        def val(field_name):
            if program is None:
                return ""
            return (getattr(program, field_name, None) or "").strip()

        return Response({
            "company_name": val("legal_company_name"),
            "representative": val("legal_representative"),
            "business_number": val("legal_business_number"),
            "ecommerce_number": val("legal_ecommerce_number"),
            "address": val("legal_address"),
            "support_email": val("legal_support_email"),
            "support_phone": val("legal_support_phone"),
            "privacy_officer_name": val("legal_privacy_officer_name"),
            "privacy_officer_contact": val("legal_privacy_officer_contact"),
            "terms_version": "1.0",
            "privacy_version": "1.0",
            "effective_date": "2026-03-14",
        }, headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
        })

    def patch(self, request):
        program = self._get_program(request)
        if not program:
            return Response({"detail": "Program not found for this tenant."}, status=404)

        LEGAL_FIELDS = {
            "company_name": ("legal_company_name", 200),
            "representative": ("legal_representative", 100),
            "business_number": ("legal_business_number", 50),
            "ecommerce_number": ("legal_ecommerce_number", 100),
            "address": ("legal_address", 500),
            "support_email": ("legal_support_email", 200),
            "support_phone": ("legal_support_phone", 50),
            "privacy_officer_name": ("legal_privacy_officer_name", 100),
            "privacy_officer_contact": ("legal_privacy_officer_contact", 200),
        }

        update_fields = []
        for api_key, (model_field, max_len) in LEGAL_FIELDS.items():
            if api_key in request.data:
                value = (request.data.get(api_key) or "").strip()[:max_len]
                setattr(program, model_field, value)
                update_fields.append(model_field)

        if update_fields:
            program.save(update_fields=update_fields)

        return Response({
            "company_name": (program.legal_company_name or "").strip(),
            "representative": (program.legal_representative or "").strip(),
            "business_number": (program.legal_business_number or "").strip(),
            "ecommerce_number": (program.legal_ecommerce_number or "").strip(),
            "address": (program.legal_address or "").strip(),
            "support_email": (program.legal_support_email or "").strip(),
            "support_phone": (program.legal_support_phone or "").strip(),
            "privacy_officer_name": (program.legal_privacy_officer_name or "").strip(),
            "privacy_officer_contact": (program.legal_privacy_officer_contact or "").strip(),
            "terms_version": "1.0",
            "privacy_version": "1.0",
            "effective_date": "2026-03-14",
        })
