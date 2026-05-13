# apps/support/messaging/views/template_views.py
"""
메시지 템플릿 CRUD 뷰 — 목록, 상세, 기본 지정, 복제, 검수 신청
"""


from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.messaging.models import MessageTemplate
from apps.domains.messaging.solapi_template_client import (
    create_kakao_template,
    list_kakao_templates,
    validate_template_variables,
)
from apps.domains.messaging.serializers import MessageTemplateSerializer


class MessageTemplateListCreateView(APIView):
    """GET: 템플릿 목록 (category 쿼리로 필터). POST: 템플릿 생성"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        qs = MessageTemplate.objects.filter(tenant=request.tenant).order_by("-updated_at")
        category = (request.query_params.get("category") or "").strip().lower()
        valid_cats = {c.value for c in MessageTemplate.Category}
        if category and category in valid_cats:
            qs = qs.filter(category=category)

        # include_system=true: 오너 테넌트의 승인 알림톡 템플릿을 시스템 기본으로 포함
        # (자체 PFID 없는 테넌트가 알림톡 발송 시 시스템 기본 채널+템플릿 사용)
        include_system = (request.query_params.get("include_system") or "").strip().lower() in ("true", "1")
        result = MessageTemplateSerializer(qs, many=True).data
        if include_system:
            from apps.domains.messaging.policy import get_owner_tenant_id
            owner_id = get_owner_tenant_id()
            if int(request.tenant.id) != owner_id:
                system_qs = MessageTemplate.objects.filter(
                    tenant_id=owner_id,
                    solapi_status="APPROVED",
                ).exclude(
                    category="signup",
                ).order_by("-updated_at")
                if category and category in valid_cats:
                    system_qs = system_qs.filter(category=category)
                result = list(result) + MessageTemplateSerializer(system_qs, many=True).data
        return Response(result)

    def post(self, request):
        serializer = MessageTemplateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # 사용자가 생성하는 템플릿은 항상 is_system=False
        serializer.save(tenant=request.tenant, is_system=False)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MessageTemplateDetailView(APIView):
    """GET/PATCH/DELETE: 단일 템플릿. 시스템 양식은 수정/삭제 차단."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def _get_template(self, request, pk):
        return MessageTemplate.objects.filter(tenant=request.tenant, pk=pk).first()

    def get(self, request, pk):
        t = self._get_template(request, pk)
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(MessageTemplateSerializer(t).data)

    def patch(self, request, pk):
        t = self._get_template(request, pk)
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if t.is_system:
            return Response(
                {"detail": "시스템 기본 양식은 수정할 수 없습니다. '복제' 후 수정해 주세요."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = MessageTemplateSerializer(t, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        data = serializer.data
        # 변수 유효성 경고 (soft validation — 저장은 허용, 경고만 반환)
        body = (t.body or "")
        import re as _re
        used_vars = set(_re.findall(r"#\{([^}]+)\}", body))
        if used_vars:
            KNOWN_VARS = {
                "학원이름", "학원명", "학생이름", "학생이름2", "학생이름3",
                "사이트링크", "강의명", "차시명", "날짜", "시간", "장소",
                "클리닉장소", "클리닉날짜", "클리닉시간", "클리닉명",
                "클리닉기존일정", "클리닉변동사항", "클리닉수정자",
                "강의날짜", "강의시간", "시험명", "과제명", "성적", "시험성적",
                "클리닉합불", "납부금액", "청구월", "반이름",
                "공지내용", "내용", "선생님메모",
                # 가입용
                "학생아이디", "학생비밀번호", "학부모아이디", "학부모비밀번호",
                "비밀번호안내", "인증번호",
            }
            unknown = used_vars - KNOWN_VARS
            if unknown:
                data["warnings"] = [f"인식할 수 없는 변수: #{{{v}}} — 발송 시 빈 값으로 대체됩니다." for v in sorted(unknown)]
        return Response(data)

    def delete(self, request, pk):
        t = self._get_template(request, pk)
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if t.is_system:
            return Response(
                {"detail": "시스템 기본 양식은 삭제할 수 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )
        t.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessageTemplateSetDefaultView(APIView):
    """POST: 해당 템플릿을 해당 카테고리의 기본 양식으로 지정 (tenant+category당 1개)."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, pk):
        t = MessageTemplate.objects.filter(tenant=request.tenant, pk=pk).first()
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        # 같은 tenant+category의 기존 기본 해제
        MessageTemplate.objects.filter(
            tenant=request.tenant, category=t.category, is_user_default=True,
        ).exclude(pk=pk).update(is_user_default=False)
        # 토글: 이미 기본이면 해제, 아니면 설정
        t.is_user_default = not t.is_user_default
        t.save(update_fields=["is_user_default"])
        return Response(MessageTemplateSerializer(t).data)


class MessageTemplateDuplicateView(APIView):
    """POST: 시스템/기존 양식을 복제하여 내 양식으로 저장."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, pk):
        src = MessageTemplate.objects.filter(tenant=request.tenant, pk=pk).first()
        if not src:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        # 요청에 name이 있으면 사용, 없으면 원본 이름 + " (복사본)"
        new_name = (request.data.get("name") or "").strip()
        if not new_name:
            new_name = f"{src.name} (복사본)"
        dup = MessageTemplate.objects.create(
            tenant=request.tenant,
            category=src.category,
            name=new_name,
            subject=src.subject,
            body=src.body,
            is_system=False,
            is_user_default=False,
        )
        return Response(MessageTemplateSerializer(dup).data, status=status.HTTP_201_CREATED)


class MessageTemplateSubmitReviewView(APIView):
    """
    POST: 해당 템플릿을 솔라피에 알림톡 템플릿으로 등록(검수 신청).
    - 테넌트 PFID 사용
    - #{변수명} 검증 후 솔라피 API 호출
    - 응답 templateId 및 PENDING 상태 DB 저장
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, pk):
        from django.conf import settings

        t = MessageTemplate.objects.filter(tenant=request.tenant, pk=pk).first()
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        tenant = request.tenant
        provider = (tenant.messaging_provider or "solapi").strip().lower()

        if provider == "ppurio":
            return Response(
                {"detail": "뿌리오는 알림톡 템플릿 검수를 뿌리오 관리자 페이지(ppurio.com)에서 직접 진행해야 합니다. "
                           "승인된 템플릿 코드를 받은 뒤, 이 템플릿의 템플릿 ID 필드에 해당 코드를 입력해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # PFID: 테넌트 직접 연동 > 시스템 기본
        pfid = (tenant.kakao_pfid or "").strip()
        if not pfid:
            default_pf_id = (getattr(settings, "SOLAPI_KAKAO_PF_ID", None) or "").strip()
            pfid = default_pf_id
        if not pfid:
            return Response(
                {"detail": "카카오 채널(PFID)이 연동되지 않았습니다. 메시징 설정에서 PFID를 등록해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 자체 솔라피 키 우선, 없으면 시스템 키
        if tenant.own_solapi_api_key and tenant.own_solapi_api_secret:
            api_key = tenant.own_solapi_api_key
            api_secret = tenant.own_solapi_api_secret
        else:
            api_key = getattr(settings, "SOLAPI_API_KEY", None) or ""
            api_secret = getattr(settings, "SOLAPI_API_SECRET", None) or ""
        if not api_key or not api_secret:
            return Response(
                {"detail": "솔라피 API 키가 설정되지 않았습니다. 직접 연동 모드에서 API 키를 먼저 등록하세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # 변수 형식 검증 (본문 + 제목)
        ok, errs = validate_template_variables(t.body, t.subject or "")
        if not ok:
            return Response(
                {"detail": "변수 검증 실패: " + "; ".join(errs)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 알림톡 content: 제목 + 본문 (제목이 있으면 첫 줄로)
        content = (t.subject.strip() + "\n" + t.body).strip() if t.subject else t.body

        try:
            result = create_kakao_template(
                api_key=api_key,
                api_secret=api_secret,
                channel_id=pfid,
                name=t.name,
                content=content,
                category_code="TE",
            )
            template_id = result.get("templateId", "")
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        t.solapi_template_id = template_id
        t.solapi_status = "PENDING"
        t.save(update_fields=["solapi_template_id", "solapi_status", "updated_at"])

        serializer = MessageTemplateSerializer(t)
        return Response(
            {"detail": "검수 신청이 완료되었습니다. 카카오 검수는 영업일 기준 1~3일 소요됩니다.", "template": serializer.data},
            status=status.HTTP_200_OK,
        )


class SolapiSyncTemplatesView(APIView):
    """
    POST: 솔라피 콘솔의 알림톡 템플릿을 SaaS DB와 동기화.

    학원장 임근혁 보고 (2026-05-13): "솔라피에 내가 남긴 템플릿이랑 시스템에 있는
    프로그램 템플릿이 일치하지 않는다." — 솔라피 콘솔에서 직접 본문/상태가 변경되거나
    검수 결과(APPROVED/REJECTED)가 갱신돼도 SaaS DB가 그걸 모르고 stale 상태로 남는
    구조였음. 본 뷰가 GET API로 콘솔의 진실을 끌어와서 SaaS DB를 truth와 맞춤.

    매칭: solapi_template_id 키.
    - SaaS DB에 같은 templateId 있음 → name/body/solapi_status 갱신 (솔라피=truth)
    - SaaS DB에 없음 → solapi_only에 기록, import 안 함 (이름 기반 자동 매칭은 위험)
      → 학원장이 응답을 보고 필요하면 별도 import 로직(Phase 2)으로

    API 키·PFID 결정 우선순위:
    - 자체 연동 학원: own_solapi_* + tenant.kakao_pfid
    - 시스템 채널 폴백 학원: settings.SOLAPI_API_KEY/SECRET + settings.SOLAPI_KAKAO_PF_ID
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        from django.conf import settings

        tenant = request.tenant
        provider = (tenant.messaging_provider or "solapi").strip().lower()
        if provider != "solapi":
            return Response(
                {"detail": "솔라피 외 공급자는 자동 동기화를 지원하지 않습니다. 뿌리오는 관리자 페이지에서 직접 확인해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # API 키: 자체 > 시스템
        if tenant.own_solapi_api_key and tenant.own_solapi_api_secret:
            api_key = tenant.own_solapi_api_key
            api_secret = tenant.own_solapi_api_secret
            credential_source = "tenant"
        else:
            api_key = (getattr(settings, "SOLAPI_API_KEY", None) or "").strip()
            api_secret = (getattr(settings, "SOLAPI_API_SECRET", None) or "").strip()
            credential_source = "system"
        if not api_key or not api_secret:
            return Response(
                {"detail": "솔라피 API 키가 설정되지 않았습니다. 메시징 설정에서 연동 정보를 등록해 주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # PFID: 자체 > 시스템
        pfid = (tenant.kakao_pfid or "").strip()
        if not pfid:
            pfid = (getattr(settings, "SOLAPI_KAKAO_PF_ID", None) or "").strip()
        if not pfid:
            return Response(
                {"detail": "카카오 채널(PFID)이 설정되지 않았습니다. 메시징 설정에서 PFID를 등록해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 솔라피 콘솔에서 GET
        try:
            solapi_list = list_kakao_templates(api_key, api_secret, pfid)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # sync 대상 tenant — 시스템 채널 학원은 owner tenant 양식도 함께 갱신
        # (오너 양식이 시스템 4종 SSOT이므로 솔라피 변경 시 모든 학원에 반영)
        from apps.domains.messaging.policy import get_owner_tenant_id
        owner_id = get_owner_tenant_id()
        target_tenant_ids = {tenant.id}
        if credential_source == "system":
            target_tenant_ids.add(owner_id)

        existing_by_solapi_id: dict[str, MessageTemplate] = {}
        for t in MessageTemplate.objects.filter(
            tenant_id__in=target_tenant_ids,
        ).exclude(solapi_template_id=""):
            tid = (t.solapi_template_id or "").strip()
            if tid:
                existing_by_solapi_id[tid] = t

        VALID_STATUSES = {"APPROVED", "PENDING", "REJECTED", "INSPECTING"}
        STATUS_NORMALIZE = {"INSPECTING": "PENDING"}

        updated_count = 0
        unchanged_count = 0
        solapi_only: list[dict] = []
        errors: list[str] = []

        for item in solapi_list:
            tid = (item.get("templateId") or item.get("id") or "").strip()
            if not tid:
                continue
            content = (item.get("content") or "").strip()
            name = (item.get("name") or "").strip()
            raw_status = (item.get("status") or "").upper().strip()
            mapped_status = STATUS_NORMALIZE.get(raw_status, raw_status)
            if mapped_status not in VALID_STATUSES and mapped_status:
                # 알 수 없는 상태는 보존하지 않고 빈 값으로
                mapped_status = ""

            tpl = existing_by_solapi_id.get(tid)
            if not tpl:
                solapi_only.append({
                    "templateId": tid,
                    "name": name,
                    "status": raw_status,
                    "content_preview": content[:80],
                })
                continue

            update_fields: list[str] = []
            if name and tpl.name != name:
                tpl.name = name
                update_fields.append("name")
            if content and tpl.body != content:
                tpl.body = content
                update_fields.append("body")
            if mapped_status and tpl.solapi_status != mapped_status:
                tpl.solapi_status = mapped_status
                update_fields.append("solapi_status")
            if update_fields:
                update_fields.append("updated_at")
                try:
                    tpl.save(update_fields=update_fields)
                    updated_count += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"templateId={tid}: {e}")
            else:
                unchanged_count += 1

        detail_parts = [f"업데이트 {updated_count}건", f"변경 없음 {unchanged_count}건"]
        if solapi_only:
            detail_parts.append(f"SaaS 미등록 {len(solapi_only)}건")
        if errors:
            detail_parts.append(f"오류 {len(errors)}건")
        detail = "솔라피 동기화 완료 — " + ", ".join(detail_parts) + "."

        return Response({
            "detail": detail,
            "updated": updated_count,
            "unchanged": unchanged_count,
            "solapi_only_count": len(solapi_only),
            "solapi_only": solapi_only[:20],  # 응답 크기 제한
            "errors": errors,
            "credential_source": credential_source,
            "pfid": pfid,
        })
