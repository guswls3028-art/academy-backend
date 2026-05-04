# PATH: apps/domains/matchup/views.py
# 매치업 API views — 문서 CRUD + 문제 조회 + 유사 검색

from __future__ import annotations

import logging

from django.http import JsonResponse, HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from apps.core.authentication import TokenVersionJWTAuthentication as JWTAuthentication

from .models import MatchupDocument, MatchupProblem, MatchupHitReport, MatchupHitReportEntry
from .serializers import (
    MatchupDocumentSerializer,
    MatchupDocumentUpdateSerializer,
    MatchupProblemSerializer,
    SimilarProblemSerializer,
    MatchupHitReportSerializer,
)
from .services import (
    find_similar_problems,
    delete_document_with_r2,
    retry_document,
    reanalyze_document,
    exclude_page_from_matchup,
    include_page_to_matchup,
    promote_inventory_to_matchup,
    ensure_matchup_upload_folder,
    manually_crop_problem,
    paste_image_as_problem,
    merge_problems,
    delete_problem_with_r2,
)
from apps.shared.utils.vector import cosine_similarity

logger = logging.getLogger(__name__)

try:
    from apps.infrastructure.storage.r2 import (
        upload_fileobj_to_r2_storage,
        generate_presigned_get_url_storage,
    )
except ImportError:
    upload_fileobj_to_r2_storage = None
    generate_presigned_get_url_storage = None

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB — 사용자(악용 risk 없는 학원 SaaS) 제한 풀라는 요청 반영
MAX_DOCUMENTS_PER_TENANT = 500


# ── helpers ──────────────────────────────────────────

def _tenant_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return JsonResponse({"detail": "Tenant required"}, status=400)
        return view_func(request, *args, **kwargs)
    return wrapped


def _jwt_required(view_func):
    def wrapped(request, *args, **kwargs):
        auth = JWTAuthentication()
        result = auth.authenticate(request)
        if result is None:
            return JsonResponse(
                {"detail": "Authentication required", "code": "auth_required"},
                status=401,
            )
        request.user, request.auth = result[0], result[1]
        return view_func(request, *args, **kwargs)
    return wrapped


def _is_tenant_staff(request):
    user = getattr(request, "user", None)
    tenant = getattr(request, "tenant", None)
    if not user or not tenant:
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    from apps.core.models import TenantMembership
    return TenantMembership.objects.filter(
        user=user, tenant=tenant, is_active=True,
        role__in=["owner", "admin", "teacher", "assistant"],
    ).exists()


def _is_tenant_admin(request) -> bool:
    """학원 owner/admin 권한자만. 다른 강사 보고서 access 권한.

    매치업 보고서 = 강사 1인 포트폴리오. 작성자 외에는 학원 운영진만 조회/수정 가능.
    일반 teacher/assistant는 본인 보고서만 접근.
    """
    user = getattr(request, "user", None)
    tenant = getattr(request, "tenant", None)
    if not user or not tenant:
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    from apps.core.models import TenantMembership
    return TenantMembership.objects.filter(
        user=user, tenant=tenant, is_active=True,
        role__in=["owner", "admin"],
    ).exists()


def _hit_report_writable(request, report) -> bool:
    """보고서 수정/삭제/제출/PDF 다운로드 권한.

    작성자 본인 OR 학원 admin/owner. 그 외(다른 강사)는 차단.
    legacy report(author=NULL)는 admin/owner만 — 작성자 식별 불가.
    """
    user = getattr(request, "user", None)
    if not user:
        return False
    if getattr(user, "is_superuser", False):
        return True
    user_id = getattr(user, "id", None)
    if user_id and report.author_id and report.author_id == user_id:
        return True
    return _is_tenant_admin(request)


def _reconcile_document_from_ai_job(doc: MatchupDocument) -> bool:
    """AIJob은 끝났지만 RDS 고갈 등으로 domain callback이 실패한 문서를 복구한다.

    업로드 직후 대량 처리 중 DB connection slot이 고갈되면 AI job은 DONE인데
    MatchupDocument만 processing에 남을 수 있다. 목록/상태 조회 시 DB의 AIResult를
    다시 적용해 멱등 복구한다.
    """
    if doc.status not in ("pending", "processing") or not doc.ai_job_id:
        return False

    try:
        from apps.domains.ai.models import AIJobModel, AIResultModel
        from apps.domains.ai.callbacks import _handle_matchup_ai_result

        job = AIJobModel.objects.filter(
            job_id=doc.ai_job_id,
            tenant_id=str(doc.tenant_id),
            job_type="matchup_analysis",
        ).first()
        if not job:
            return False

        if job.source_id and str(job.source_id) != str(doc.id):
            logger.error(
                "MATCHUP_RECONCILE_SOURCE_MISMATCH | doc_id=%s | job_id=%s | job_source_id=%s",
                doc.id, doc.ai_job_id, job.source_id,
            )
            return False

        if job.status == "DONE":
            result = AIResultModel.objects.filter(job=job).first()
            if not result or not isinstance(result.payload, dict):
                logger.warning(
                    "MATCHUP_RECONCILE_NO_RESULT | doc_id=%s | job_id=%s",
                    doc.id, doc.ai_job_id,
                )
                return False
            _handle_matchup_ai_result(
                job_id=job.job_id,
                status="DONE",
                result_payload=result.payload,
                error=None,
                source_id=str(doc.id),
            )
            doc.refresh_from_db(fields=["status", "problem_count", "error_message", "meta"])
            return True

        if job.status in ("FAILED", "REJECTED_BAD_INPUT", "REVIEW_REQUIRED"):
            _handle_matchup_ai_result(
                job_id=job.job_id,
                status="FAILED",
                result_payload={},
                error=job.error_message or job.last_error or "AI 분석 실패",
                source_id=str(doc.id),
            )
            doc.refresh_from_db(fields=["status", "problem_count", "error_message", "meta"])
            return True
    except Exception:
        logger.exception(
            "MATCHUP_RECONCILE_FAILED | doc_id=%s | job_id=%s",
            doc.id, doc.ai_job_id,
        )

    return False


# ── Document views ───────────────────────────────────

@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentUploadView(View):
    """POST /api/v1/matchup/documents/upload/

    Storage-as-canonical: 매치업 페이지 업로드는 내부적으로
      1) InventoryFile 생성 (admin scope, /매치업-업로드/{YYYY-MM}/ 폴더)
      2) MatchupDocument 즉시 승격 + dispatch
    사용자 체감은 1-step 유지.
    """

    def post(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        file = request.FILES.get("file")
        if not file:
            return JsonResponse({"detail": "file required"}, status=400)

        if file.content_type not in ALLOWED_CONTENT_TYPES:
            return JsonResponse(
                {"detail": "지원하지 않는 형식입니다. PDF, PNG, JPG만 가능합니다."},
                status=400,
            )

        if file.size > MAX_FILE_SIZE:
            return JsonResponse(
                {"detail": "파일 크기가 2GB를 초과합니다."},
                status=400,
            )

        tenant = request.tenant
        doc_count = MatchupDocument.objects.filter(tenant=tenant).count()
        if doc_count >= MAX_DOCUMENTS_PER_TENANT:
            return JsonResponse(
                {"detail": f"테넌트당 최대 {MAX_DOCUMENTS_PER_TENANT}개 문서까지 업로드 가능합니다."},
                status=400,
            )

        if not upload_fileobj_to_r2_storage:
            return JsonResponse({"detail": "Storage not configured"}, status=500)

        title = request.POST.get("title", "") or file.name
        category = request.POST.get("category", "")
        subject = request.POST.get("subject", "")
        grade_level = request.POST.get("grade_level", "")
        # source_type 7-value SSOT (2026-05-02~) — legacy 2-value 입력도 매핑 수용.
        # 학원장 directive: 자료 유형이 분리 strategy 분기의 1순위 신호.
        from apps.domains.matchup.source_types import normalize_source_type
        upload_intent = normalize_source_type(request.POST.get("intent") or request.POST.get("source_type"))

        # 1) /매치업-업로드/{YYYY-MM}/ 폴더에 InventoryFile 생성
        from apps.domains.inventory.r2_path import build_r2_key, safe_filename, folder_path_string
        from apps.domains.inventory.models import InventoryFile

        ym_folder = ensure_matchup_upload_folder(tenant)
        path_parts = []
        p = ym_folder
        while p:
            path_parts.append(p.name)
            p = p.parent
        folder_path = folder_path_string(list(reversed(path_parts)))

        safe_name = safe_filename(file.name)
        r2_key = build_r2_key(
            tenant_id=tenant.id,
            scope="admin",
            student_ps="",
            folder_path=folder_path,
            file_name=safe_name,
        )

        try:
            upload_fileobj_to_r2_storage(
                fileobj=file,
                key=r2_key,
                content_type=file.content_type,
            )
        except Exception as e:
            return JsonResponse({"detail": f"R2 upload failed: {e}"}, status=502)

        inv_file = InventoryFile.objects.create(
            tenant=tenant,
            scope="admin",
            student_ps="",
            folder=ym_folder,
            display_name=title,
            description="",
            icon="file-text",
            r2_key=r2_key,
            original_name=file.name,
            size_bytes=file.size,
            content_type=file.content_type,
        )

        # 2) 즉시 승격 + dispatch (intent를 promote에 전달 → meta + payload 양쪽에 기록).
        # author=request.user — 자료를 업로드한 강사를 매치업 격리 baseline으로 등록.
        doc = promote_inventory_to_matchup(
            inv_file,
            title=title,
            category=category,
            subject=subject,
            grade_level=grade_level,
            upload_intent=upload_intent,
            author=getattr(request, "user", None),
        )

        data = MatchupDocumentSerializer(doc).data
        return JsonResponse(data, status=201)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentPromoteFromInventoryView(View):
    """POST /api/v1/matchup/documents/promote/

    body: { inventory_file_id: int, title?: str, category?: str, subject?: str, grade_level?: str }
    저장소 admin scope 파일을 매치업 분석 대상으로 승격.
    """

    def post(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        import json
        from django.db import IntegrityError, transaction
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        inv_file_id = body.get("inventory_file_id")
        if not inv_file_id:
            return JsonResponse({"detail": "inventory_file_id required"}, status=400)

        from apps.domains.inventory.models import InventoryFile
        try:
            inv_file = InventoryFile.objects.get(
                id=int(inv_file_id), tenant=request.tenant,
            )
        except (InventoryFile.DoesNotExist, ValueError, TypeError):
            return JsonResponse({"detail": "Not found"}, status=404)

        if inv_file.scope != "admin":
            return JsonResponse(
                {"detail": "선생님 저장소(admin scope) 파일만 매치업으로 승격할 수 있습니다."},
                status=400,
            )

        if inv_file.content_type not in ALLOWED_CONTENT_TYPES:
            return JsonResponse(
                {"detail": f"매치업은 PDF/PNG/JPG만 지원합니다. (현재: {inv_file.content_type})"},
                status=400,
            )

        # 사전 중복 검사 — 일반 케이스에서 빠르게 차단
        existing = MatchupDocument.objects.filter(
            tenant=request.tenant, inventory_file=inv_file,
        ).first()
        if existing:
            return JsonResponse(
                {
                    "detail": "이미 매치업 자료로 등록되어 있습니다.",
                    "code": "already_promoted",
                    "document_id": existing.id,
                },
                status=409,
            )

        doc_count = MatchupDocument.objects.filter(tenant=request.tenant).count()
        if doc_count >= MAX_DOCUMENTS_PER_TENANT:
            return JsonResponse(
                {"detail": f"테넌트당 최대 {MAX_DOCUMENTS_PER_TENANT}개 문서까지 업로드 가능합니다."},
                status=400,
            )

        title = (body.get("title") or "").strip() or inv_file.display_name
        category = body.get("category", "")
        subject = body.get("subject", "")
        grade_level = body.get("grade_level", "")
        # source_type 7-value SSOT (2026-05-02~) — legacy 2-value 입력도 매핑 수용.
        from apps.domains.matchup.source_types import normalize_source_type
        upload_intent = normalize_source_type(body.get("intent") or body.get("source_type"))

        # Race-safe 승격 — 사전 검사 후 race가 통과해도 OneToOne unique IntegrityError로 차단.
        try:
            with transaction.atomic():
                doc = promote_inventory_to_matchup(
                    inv_file,
                    title=title,
                    category=category,
                    subject=subject,
                    grade_level=grade_level,
                    upload_intent=upload_intent,
                    author=getattr(request, "user", None),
                )
        except IntegrityError:
            existing = MatchupDocument.objects.filter(
                tenant=request.tenant, inventory_file=inv_file,
            ).first()
            if existing:
                return JsonResponse(
                    {
                        "detail": "이미 매치업 자료로 등록되어 있습니다.",
                        "code": "already_promoted",
                        "document_id": existing.id,
                    },
                    status=409,
                )
            raise

        data = MatchupDocumentSerializer(doc).data
        return JsonResponse(data, status=201)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentListView(View):
    """GET /api/v1/matchup/documents/"""

    def get(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        docs = list(MatchupDocument.objects.filter(tenant=request.tenant))
        for doc in docs:
            _reconcile_document_from_ai_job(doc)
        data = MatchupDocumentSerializer(docs, many=True).data
        return JsonResponse(data, safe=False)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentDetailView(View):
    """
    PATCH /api/v1/matchup/documents/<id>/  — 수정
    DELETE /api/v1/matchup/documents/<id>/ — 삭제
    """

    def _get_doc(self, request, doc_id):
        try:
            return MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return None

    def patch(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        doc = self._get_doc(request, doc_id)
        if not doc:
            return JsonResponse({"detail": "Not found"}, status=404)

        import json
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        ser = MatchupDocumentUpdateSerializer(data=body)
        if not ser.is_valid():
            return JsonResponse(ser.errors, status=400)

        update_fields = ["updated_at"]
        for field in ("title", "category", "subject", "grade_level"):
            if field in ser.validated_data:
                setattr(doc, field, ser.validated_data[field])
                update_fields.append(field)
        # source_type 7-value SSOT — Phase 1A 후속 (post-upload 보정).
        # 학원장이 백필 결과를 검수하면서 잘못 분류된 doc을 즉시 정정 가능.
        if "source_type" in ser.validated_data or "intent" in ser.validated_data:
            from apps.domains.matchup.source_types import normalize_source_type, is_indexable
            new_st = normalize_source_type(
                ser.validated_data.get("source_type") or ser.validated_data.get("intent")
            )
            meta = dict(doc.meta or {})
            meta["source_type"] = new_st
            meta["upload_intent"] = new_st          # legacy alias 동기화
            meta["indexable"] = is_indexable(new_st)
            meta["document_role"] = (
                "exam_sheet" if new_st in ("school_exam_pdf", "student_exam_photo")
                else "reference_material"
            )
            # 학원장 직접 변경 마커 (백필 마커 우선순위 낮춤)
            meta["source_type_user_override"] = True
            meta.pop("source_type_backfilled", None)
            doc.meta = meta
            update_fields.append("meta")

        doc.save(update_fields=update_fields)
        return JsonResponse(MatchupDocumentSerializer(doc).data)

    def delete(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        doc = self._get_doc(request, doc_id)
        if not doc:
            return JsonResponse({"detail": "Not found"}, status=404)

        delete_document_with_r2(doc)
        return JsonResponse({"ok": True})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class CategoryListView(View):
    """GET /api/v1/matchup/categories/

    카테고리별 문서 카운트 집계. 미분류는 빈 문자열("")로 반환.
    """

    def get(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        from django.db.models import Count, Q
        rows = (
            MatchupDocument.objects.filter(tenant=request.tenant)
            .values("category")
            .annotate(
                total=Count("id"),
                tests=Count(
                    "id",
                    filter=Q(meta__upload_intent="test") | Q(meta__document_role="exam_sheet"),
                ),
            )
            .order_by("category")
        )
        result = [
            {
                "name": (r["category"] or "").strip(),
                "total": r["total"],
                "tests": r["tests"],
                "references": r["total"] - r["tests"],
            }
            for r in rows
        ]
        return JsonResponse(result, safe=False)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class CategoryRenameView(View):
    """POST /api/v1/matchup/categories/rename/

    body: { from: str, to: str }
    `to`가 이미 존재하면 자연스럽게 병합(merge)된다 — 동일한 SQL UPDATE 한 번.
    빈 문자열 to는 "미분류로 이동"과 동일.
    """

    def post(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        from_name = (body.get("from") or "").strip()
        to_name = (body.get("to") or "").strip()
        # from은 빈 값 허용 안 함 — 미분류 문서를 일괄 카테고리화하려면 assign 사용.
        if not from_name:
            return JsonResponse({"detail": "from은 빈 값일 수 없습니다."}, status=400)
        if len(to_name) > 100:
            return JsonResponse({"detail": "카테고리 이름이 너무 깁니다 (100자 이내)."}, status=400)
        if from_name == to_name:
            return JsonResponse({"updated": 0, "category": to_name})

        updated = MatchupDocument.objects.filter(
            tenant=request.tenant, category=from_name,
        ).update(category=to_name)
        return JsonResponse({"updated": updated, "category": to_name})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class CategoryAssignView(View):
    """POST /api/v1/matchup/categories/assign/

    body: { document_ids: [int], category: str }
    여러 문서에 카테고리 일괄 부여. 빈 문자열이면 미분류로 이동.
    """

    def post(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        ids = body.get("document_ids") or []
        if not isinstance(ids, list) or not ids:
            return JsonResponse({"detail": "document_ids 필수 (배열)"}, status=400)
        try:
            int_ids = [int(x) for x in ids]
        except (TypeError, ValueError):
            return JsonResponse({"detail": "document_ids는 정수 배열이어야 합니다."}, status=400)

        category = (body.get("category") or "").strip()
        if len(category) > 100:
            return JsonResponse({"detail": "카테고리 이름이 너무 깁니다 (100자 이내)."}, status=400)

        updated = MatchupDocument.objects.filter(
            tenant=request.tenant, id__in=int_ids,
        ).update(category=category)
        return JsonResponse({"updated": updated, "category": category})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentCrossMatchesView(View):
    """GET /api/v1/matchup/documents/<id>/cross-matches/?top_k=1

    이 doc(주로 학생 시험지)의 모든 problem에 대해 다른 doc의 problem 중
    가장 유사한 top_k건 반환. 같은 doc 안의 problem은 제외(cross-doc only).

    응답 구조:
    {
      "doc_id": int,
      "doc_title": str,
      "matches": [
        {
          "problem_id": int,
          "problem_number": int,
          "problem_text_preview": str,  # 처음 80자
          "best_matches": [
            {"document_id": int, "document_title": str,
             "problem_number": int, "similarity": float},
            ...
          ]
        }, ...
      ]
    }
    """

    def get(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            top_k = max(1, min(int(request.GET.get("top_k", 1)), 5))
        except (TypeError, ValueError):
            top_k = 1

        problems = list(
            doc.problems.exclude(embedding__isnull=True)
            .only("id", "number", "text", "embedding")
            .order_by("number")
        )
        candidates = list(
            MatchupProblem.objects
            .filter(
                tenant=request.tenant,
                embedding__isnull=False,
                document__isnull=False,  # exam-source problem (document=None) 제외
            )
            .exclude(document_id=doc.id)
            .exclude(document__meta__upload_intent="test")
            .exclude(document__meta__document_role="exam_sheet")
            .select_related("document")
            .only(
                "id", "document_id", "number", "embedding",
                "document__id", "document__title", "document__category", "document__meta",
            )
        )
        # 카테고리 격리 — 빈 카테고리도 빈 카테고리끼리만 매칭.
        # 사용자 피드백 2026-04-29: 카테고리 누락 시 다른 학교 자료가 leak되던 버그 차단.
        source_category = (doc.category or "").strip()
        candidates = [
            c for c in candidates
            if c.document is not None
            and (c.document.category or "").strip() == source_category
        ]

        matches = []
        for p in problems:
            scored = []
            for c in candidates:
                if not c.embedding:
                    continue
                scored.append((c, cosine_similarity(p.embedding, c.embedding)))
            scored.sort(key=lambda item: item[1], reverse=True)

            best_matches = [
                {
                    "document_id": sp.document_id,
                    "document_title": sp.document.title if sp.document_id else "",
                    "problem_number": sp.number,
                    "similarity": round(sim, 4),
                }
                for sp, sim in scored[:top_k]
            ]

            matches.append({
                "problem_id": p.id,
                "problem_number": p.number,
                "problem_text_preview": (p.text or "")[:80],
                "best_matches": best_matches,
            })

        return JsonResponse({
            "doc_id": doc.id,
            "doc_title": doc.title,
            "problem_count": len(matches),
            "matches": matches,
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentPreviewView(View):
    """GET /api/v1/matchup/documents/<id>/preview/

    원본 PDF/이미지 미리보기용 presigned URL 반환.
    프론트의 미리보기 모달이 iframe(PDF) 또는 img(이미지)에서 사용.
    """

    def get(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not generate_presigned_get_url_storage:
            return JsonResponse({"detail": "Storage not configured"}, status=500)
        # filename 안 넘기면 ResponseContentDisposition 미설정 → 브라우저가 inline 처리
        url = generate_presigned_get_url_storage(
            key=doc.r2_key, expires_in=3600,
            content_type=doc.content_type or None,
        )
        return JsonResponse({
            "url": url,
            "content_type": doc.content_type,
            "title": doc.title,
            "original_name": doc.original_name,
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentRetryView(View):
    """POST /api/v1/matchup/documents/<id>/retry/"""

    def post(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        if doc.status not in ("failed",):
            return JsonResponse({"detail": "재시도는 실패 상태에서만 가능합니다."}, status=400)

        try:
            job_id = retry_document(doc)
        except Exception:
            logger.exception("retry_document failed for doc %s", doc.id)
            return JsonResponse({"detail": "재시도 실패"}, status=500)

        doc.refresh_from_db()
        return JsonResponse(MatchupDocumentSerializer(doc).data)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentJobView(View):
    """GET /api/v1/matchup/documents/<id>/job/

    업로드 직후 응답에 ai_job_id가 비어있는 경우(구버전/과도기) 프론트 fallback에서 사용.
    tenant + staff 범위 내에서만 조회 가능.
    """

    def get(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        _reconcile_document_from_ai_job(doc)
        return JsonResponse(
            {
                "document_id": doc.id,
                "status": doc.status,
                "ai_job_id": doc.ai_job_id or "",
                "problem_count": doc.problem_count,
                "title": doc.title,
            }
        )


# ── Problem views ────────────────────────────────────

@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class ProblemListView(View):
    """GET /api/v1/matchup/problems/?document_id=X

    이미지 presigned URL을 serializer 출력에 합쳐서 반환한다.
    카드별 N+1 presign 요청을 제거하기 위함.
    """

    def get(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        qs = MatchupProblem.objects.filter(tenant=request.tenant)
        doc_id = request.GET.get("document_id")
        if doc_id:
            qs = qs.filter(document_id=doc_id)
        data = MatchupProblemSerializer(qs, many=True).data

        if generate_presigned_get_url_storage:
            for row in data:
                key = row.get("image_key")
                if key:
                    row["image_url"] = generate_presigned_get_url_storage(
                        key=key, expires_in=3600
                    )

        return JsonResponse(data, safe=False)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class ProblemDetailView(View):
    """
    GET    /api/v1/matchup/problems/<id>/  — 단건 조회
    PATCH  /api/v1/matchup/problems/<id>/  — number/text 수정
    DELETE /api/v1/matchup/problems/<id>/  — 단건 삭제 + R2 cleanup
    """

    def _get_problem(self, request, problem_id):
        try:
            return MatchupProblem.objects.get(id=problem_id, tenant=request.tenant)
        except MatchupProblem.DoesNotExist:
            return None

    def get(self, request, problem_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        problem = self._get_problem(request, problem_id)
        if not problem:
            return JsonResponse({"detail": "Not found"}, status=404)

        data = MatchupProblemSerializer(problem).data

        # 이미지 presigned URL 추가
        if problem.image_key and generate_presigned_get_url_storage:
            data["image_url"] = generate_presigned_get_url_storage(
                key=problem.image_key, expires_in=3600
            )

        return JsonResponse(data)

    def patch(self, request, problem_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        problem = self._get_problem(request, problem_id)
        if not problem:
            return JsonResponse({"detail": "Not found"}, status=404)

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        update_fields = ["updated_at"]
        if "number" in body:
            try:
                new_num = int(body["number"])
            except (TypeError, ValueError):
                return JsonResponse({"detail": "number must be an integer"}, status=400)
            if not (1 <= new_num <= 999):
                return JsonResponse({"detail": "number out of range"}, status=400)
            problem.number = new_num
            update_fields.append("number")
        if "text" in body and isinstance(body["text"], str):
            problem.text = body["text"]
            update_fields.append("text")

        try:
            problem.save(update_fields=update_fields)
        except Exception as e:
            # unique constraint 등
            return JsonResponse({"detail": str(e)}, status=409)

        data = MatchupProblemSerializer(problem).data
        if problem.image_key and generate_presigned_get_url_storage:
            data["image_url"] = generate_presigned_get_url_storage(
                key=problem.image_key, expires_in=3600
            )
        return JsonResponse(data)

    def delete(self, request, problem_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        problem = self._get_problem(request, problem_id)
        if not problem:
            return JsonResponse({"detail": "Not found"}, status=404)

        delete_problem_with_r2(problem)
        return JsonResponse({"ok": True})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentManualCropView(View):
    """POST /api/v1/matchup/documents/<id>/manual-crop/

    body: {
      page_index: 0,
      bbox: [x, y, w, h],   # 모두 0..1 (페이지 정규화)
      number: 5,            # 1..999. 같은 번호면 덮어쓰기.
      text?: ""             # 선택 — 사용자 수동 입력 시
    }

    동기 응답: 생성/갱신된 problem (image_url 포함).
    embedding은 워커가 비동기로 채움.
    """

    def post(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        try:
            page_index = int(body.get("page_index", 0))
            number = int(body["number"])
            raw_bbox = body.get("bbox") or []
            if isinstance(raw_bbox, dict):
                bbox = (
                    float(raw_bbox.get("x", 0)),
                    float(raw_bbox.get("y", 0)),
                    float(raw_bbox.get("w", 0)),
                    float(raw_bbox.get("h", 0)),
                )
            else:
                if len(raw_bbox) != 4:
                    raise ValueError("bbox length")
                bbox = (float(raw_bbox[0]), float(raw_bbox[1]), float(raw_bbox[2]), float(raw_bbox[3]))
            text = body.get("text") or ""
        except (KeyError, TypeError, ValueError) as e:
            return JsonResponse({"detail": f"잘못된 요청: {e}"}, status=400)

        try:
            problem = manually_crop_problem(
                doc,
                page_index=page_index,
                bbox_norm=bbox,
                number=number,
                text=text,
            )
        except ValueError as e:
            return JsonResponse({"detail": str(e)}, status=400)
        except Exception:
            logger.exception("manual crop failed (doc=%s)", doc.id)
            return JsonResponse({"detail": "수동 자르기 처리에 실패했습니다."}, status=500)

        data = MatchupProblemSerializer(problem).data
        if problem.image_key and generate_presigned_get_url_storage:
            data["image_url"] = generate_presigned_get_url_storage(
                key=problem.image_key, expires_in=3600,
            )
        return JsonResponse(data, status=201)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentMergeProblemsView(View):
    """POST /api/v1/matchup/documents/<id>/merge-problems/

    같은 doc 내 problem N(>=2)개를 1개로 합친다. 시험지에서 한 문항이 컬럼 경계나
    페이지 경계에 걸쳐 자동분리에 의해 분리된 경우, 사용자가 그리드에서 N개를 선택해
    하나로 묶을 수 있게 해주는 운영자 도구.
    """

    def post(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        ids = body.get("problem_ids") or []
        if not isinstance(ids, list):
            return JsonResponse({"detail": "problem_ids는 배열이어야 합니다."}, status=400)

        target_number = body.get("target_number")
        if target_number is not None:
            try:
                target_number = int(target_number)
            except (TypeError, ValueError):
                return JsonResponse({"detail": "target_number가 정수가 아닙니다."}, status=400)

        # int 변환은 service에 위임 — service가 ValueError로 응답하면 400 반환.
        try:
            problem = merge_problems(
                doc,
                problem_ids=ids,
                target_number=target_number,
            )
        except ValueError as e:
            return JsonResponse({"detail": str(e)}, status=400)
        except Exception:
            logger.exception("merge_problems failed (doc=%s)", doc.id)
            return JsonResponse({"detail": "문항 합치기 처리에 실패했습니다."}, status=500)

        data = MatchupProblemSerializer(problem).data
        if problem.image_key and generate_presigned_get_url_storage:
            data["image_url"] = generate_presigned_get_url_storage(
                key=problem.image_key, expires_in=3600,
            )
        return JsonResponse(data, status=200)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentPasteProblemView(View):
    """POST /api/v1/matchup/documents/<id>/paste-problem/

    클립보드/파일 이미지를 problem으로 직접 등록 (PDF 페이지 매뉴얼 크롭 안 거침).

    Request: multipart/form-data
      - image: 이미지 파일 (png/jpg/jpeg/webp/gif, ≤25MB)
      - number: int (1..999) 같은 번호면 덮어쓰기

    동기 응답: 생성/갱신된 problem (image_url 포함).
    embedding은 워커가 비동기로 채움 (matchup_manual_index).
    """

    def post(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        upload = request.FILES.get("image")
        if upload is None:
            return JsonResponse({"detail": "image 파일이 없습니다."}, status=400)

        try:
            number = int(request.POST.get("number", "0"))
        except (TypeError, ValueError):
            return JsonResponse({"detail": "number가 정수가 아닙니다."}, status=400)

        try:
            data_bytes = upload.read()
            problem = paste_image_as_problem(
                doc,
                image_bytes=data_bytes,
                content_type=upload.content_type or "",
                number=number,
            )
        except ValueError as e:
            return JsonResponse({"detail": str(e)}, status=400)
        except Exception:
            logger.exception("paste image as problem failed (doc=%s)", doc.id)
            return JsonResponse({"detail": "이미지 붙여넣기 처리에 실패했습니다."}, status=500)

        data = MatchupProblemSerializer(problem).data
        if problem.image_key and generate_presigned_get_url_storage:
            data["image_url"] = generate_presigned_get_url_storage(
                key=problem.image_key, expires_in=3600,
            )
        return JsonResponse(data, status=201)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentPagesView(View):
    """GET /api/v1/matchup/documents/<id>/pages/

    수동 크롭 모달을 위한 페이지 정보 + 페이지 이미지 presigned URL.
    동작: PDF면 페이지별 렌더해서 R2 임시 공간에 캐시 후 presign. 단순 이미지는 그대로.

    응답:
    {
      "doc_id": int,
      "is_pdf": bool,
      "page_count": int,
      "pages": [
         { "index": 0, "url": "...", "width": 0..1, "height": 0..1 }, ...
      ]
    }

    pages_url은 short-lived (10분). 모달이 페이지 캔버스에 그릴 때 사용.
    PDF 페이지 렌더는 매 호출마다 새로 하지 않고, doc.meta.page_image_keys 캐시 재활용.
    """

    def get(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not generate_presigned_get_url_storage:
            return JsonResponse({"detail": "Storage not configured"}, status=500)

        from .services import ensure_document_page_images
        try:
            pages = ensure_document_page_images(doc)
        except Exception:
            logger.exception("ensure_document_page_images failed (doc=%s)", doc.id)
            return JsonResponse({"detail": "페이지 이미지 준비 실패"}, status=500)

        is_pdf = (doc.content_type or "").lower() == "application/pdf"
        return JsonResponse({
            "doc_id": doc.id,
            "is_pdf": is_pdf,
            "page_count": len(pages),
            "pages": pages,
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentPageExcludeView(View):
    """POST /api/v1/matchup/documents/<doc_id>/pages/<page_idx>/exclude/

    Phase 5-deep 검수 UI: 학원장이 low_conf 페이지를 매치업 인덱싱에서 제외.
    즉시 효과: 해당 페이지 problems 삭제. 영구 효과: 다음 reanalyze 시 워커가 skip.
    """

    def post(self, request, doc_id, page_idx):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            result = exclude_page_from_matchup(doc, int(page_idx))
        except ValueError as e:
            return JsonResponse({"detail": str(e)}, status=400)
        except Exception:
            logger.exception("exclude_page_from_matchup failed (doc=%s page=%s)", doc.id, page_idx)
            return JsonResponse({"detail": "페이지 제외 실패"}, status=500)

        return JsonResponse({
            "ok": True,
            "doc_id": doc.id,
            "page_index": int(page_idx),
            **result,
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentPageIncludeView(View):
    """POST /api/v1/matchup/documents/<doc_id>/pages/<page_idx>/include/

    P1 (2026-05-04): exclude_page_from_matchup 롤백.
    학원장이 실수로 페이지를 제외했다가 복구하는 case.
    excluded_pages 리스트에서 page_index 제거. problem 복원은 reanalyze 별도 호출.
    """

    def post(self, request, doc_id, page_idx):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            result = include_page_to_matchup(doc, int(page_idx))
        except ValueError as e:
            return JsonResponse({"detail": str(e)}, status=400)
        except Exception:
            logger.exception("include_page_to_matchup failed (doc=%s page=%s)", doc.id, page_idx)
            return JsonResponse({"detail": "페이지 복원 실패"}, status=500)

        return JsonResponse({
            "ok": True,
            "doc_id": doc.id,
            "page_index": int(page_idx),
            **result,
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentPageVlmClassifyView(View):
    """POST /api/v1/matchup/documents/<doc_id>/pages/<page_idx>/vlm-classify/

    Phase 5-deep VLM 정밀 분석: low_conf 페이지 ondemand로 Gemini 호출.
    - 페이지 이미지를 R2에서 다운로드 → temp file → vision adapter 호출
    - 결과: {page_role, should_skip, problems[{number, bbox, confidence}], confidence, debug}
    - cost guard: doc당 호출 횟수 cap (vlm_fallback._VLM_DOC_CALL_LIMIT)
    """

    def post(self, request, doc_id, page_idx):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            page_idx_i = int(page_idx)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "Invalid page_idx"}, status=400)

        from .services import ensure_document_page_images
        try:
            pages = ensure_document_page_images(doc)
        except Exception:
            logger.exception("ensure_document_page_images failed (doc=%s)", doc.id)
            return JsonResponse({"detail": "페이지 이미지 준비 실패"}, status=500)

        if page_idx_i < 0 or page_idx_i >= len(pages):
            return JsonResponse({"detail": "page_idx 범위 초과"}, status=400)

        page = pages[page_idx_i]
        page_url = page.get("url")
        if not page_url:
            return JsonResponse({"detail": "페이지 URL 없음"}, status=500)

        # presigned URL → temp file 다운로드 (R2 ap-northeast 통과 시간 + 큰 PDF 페이지 고려)
        import os
        import tempfile
        import requests
        try:
            r = requests.get(page_url, timeout=60)
            r.raise_for_status()
        except Exception as e:
            logger.warning("VLM page image download fail (doc=%s page=%s): %s", doc.id, page_idx_i, e)
            return JsonResponse({"detail": "페이지 이미지 다운로드 실패"}, status=502)

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        try:
            tmp.write(r.content)
            tmp.flush()
            tmp.close()

            from academy.adapters.ai.detection.vlm_fallback import detect_problems_vision
            try:
                result = detect_problems_vision(
                    image_path=tmp.name,
                    page_meta={
                        "document_id": doc.id,
                        "page_index": page_idx_i,
                        "page_width": page.get("width"),
                        "page_height": page.get("height"),
                    },
                )
            except RuntimeError as e:
                # quota / API key missing 등
                msg = str(e)
                status_code = 429 if "한도 초과" in msg else 500
                return JsonResponse({"detail": msg}, status=status_code)
            except Exception:
                logger.exception("VLM detect_problems_vision failed (doc=%s page=%s)", doc.id, page_idx_i)
                return JsonResponse({"detail": "VLM 분석 실패"}, status=500)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        return JsonResponse({
            "ok": True,
            "doc_id": doc.id,
            "page_index": page_idx_i,
            "page_role": result.page_role.value,
            "should_skip": result.should_skip,
            "confidence": result.confidence,
            "problems": [
                {"number": p.number, "bbox": list(p.bbox), "confidence": p.confidence}
                for p in result.problems
            ],
            "debug": result.debug,
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentReanalyzeView(View):
    """POST /api/v1/matchup/documents/<doc_id>/reanalyze/

    status 무관 재분석 — Phase 5-deep 검수 UI에서 호출.
    DocumentRetryView는 failed only. 학원장 검수 후(excluded_pages 적용 /
    source_type 변경 후) done 상태에서 재처리 트리거 진입점이 필요.
    """

    def post(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            job_id = reanalyze_document(doc)
        except RuntimeError as e:
            return JsonResponse({"detail": str(e)}, status=409)
        except Exception:
            logger.exception("reanalyze_document failed (doc=%s)", doc.id)
            return JsonResponse({"detail": "재분석 실패"}, status=500)

        doc.refresh_from_db()
        return JsonResponse({**MatchupDocumentSerializer(doc).data, "job_id": job_id})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class SimilarProblemView(View):
    """POST /api/v1/matchup/problems/<id>/similar/"""

    def post(self, request, problem_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            body = {}

        top_k = min(int(body.get("top_k", 10)), 50)

        # 저작권 격리: 호출자 자신의 자료 + legacy 공용 풀만 후보.
        # admin/owner는 운영 검증 시 author=NULL로 우회 가능 (전체 풀). 일반 강사는 본인 풀.
        scope_author_id = getattr(getattr(request, "user", None), "id", None)
        if _is_tenant_admin(request) and (request.GET.get("scope") or "").lower() == "all":
            scope_author_id = None
        results = find_similar_problems(
            problem_id=problem_id,
            tenant_id=request.tenant.id,
            top_k=top_k,
            author_id=scope_author_id,
        )

        # document title 미리 조회
        doc_ids = {p.document_id for p, _ in results}
        doc_titles = dict(
            MatchupDocument.objects.filter(id__in=doc_ids, tenant=request.tenant).values_list("id", "title")
        )

        output = []
        for problem, sim in results:
            entry = {
                "id": problem.id,
                "document_id": problem.document_id,
                "document_title": doc_titles.get(problem.document_id, ""),
                "number": problem.number,
                "text": problem.text[:200],
                "similarity": round(sim, 4),
                "source_type": getattr(problem, "source_type", "matchup"),
                "source_lecture_title": getattr(problem, "source_lecture_title", ""),
                "source_session_title": getattr(problem, "source_session_title", ""),
                "source_exam_title": getattr(problem, "source_exam_title", ""),
            }
            # 이미지 URL
            if problem.image_key and generate_presigned_get_url_storage:
                entry["image_url"] = generate_presigned_get_url_storage(
                    key=problem.image_key, expires_in=3600
                )
            output.append(entry)

        return JsonResponse({"results": output})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class ProblemPresignView(View):
    """POST /api/v1/matchup/problems/presign/ — 문제 이미지 presigned URL 생성"""

    def post(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        import json
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        problem_id = body.get("problem_id")
        if not problem_id:
            return JsonResponse({"detail": "problem_id required"}, status=400)

        try:
            problem = MatchupProblem.objects.get(id=problem_id, tenant=request.tenant)
        except MatchupProblem.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        if not problem.image_key:
            return JsonResponse({"detail": "No image"}, status=404)

        if not generate_presigned_get_url_storage:
            return JsonResponse({"detail": "Storage not configured"}, status=500)

        url = generate_presigned_get_url_storage(
            key=problem.image_key, expires_in=3600
        )
        return JsonResponse({"url": url})


# ── Curated Hit Report (강사 1인의 매치업 적중 보고서) ────────────
#
# 정체성 (정정 2026-05-03):
#   매치업 보고서 = 프리랜서 강사 1인이 작성하는 3중 역할 산출물.
#     ① 수업 히스토리 (강사 자기 검토)
#     ② 제출 리포트 (소속 학원에 정기 제출하는 KPI)
#     ③ 신뢰자료+홍보물 (신규 학원/카페에서 강사 개인 브랜딩)
#   카테고리당 시험지 1장 + 강사 1명 = 보고서 1건. 강사 N명이 각자 보고서 작성 가능.

@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportListView(View):
    """GET /api/v1/matchup/hit-reports/

    강사 1인 보고서 누적 리스트. 본인 보고서 + 학원 admin/owner는 전체 조회 가능.

    Query params:
      mine=1              : 본인 작성 보고서만 (admin/owner도 본인 시점만)
      status=draft|submitted (선택)
      category=str        (선택)

    Response:
      {
        "reports": [
          { id, document_id, document_title, document_category,
            author_id, author_name, title, status, submitted_at,
            exam_count, curated_count, curated_progress, ... },
          ...
        ],
        "summary": { total, submitted, drafts }
      }
    """

    def get(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        qs = MatchupHitReport.objects.filter(tenant=request.tenant).select_related(
            "document", "author",
        )

        # 일반 강사(admin/owner 아님)는 항상 본인 보고서만. admin/owner는 mine=1로 명시 시에만.
        is_admin = _is_tenant_admin(request)
        mine = (request.GET.get("mine") or "").lower() in ("1", "true", "yes")
        user_id = getattr(getattr(request, "user", None), "id", None)
        if not is_admin or mine:
            if user_id:
                qs = qs.filter(author_id=user_id)
            else:
                qs = qs.none()

        status_filter = (request.GET.get("status") or "").strip().lower()
        if status_filter in ("draft", "submitted"):
            qs = qs.filter(status=status_filter)

        category_filter = (request.GET.get("category") or "").strip()
        if category_filter:
            qs = qs.filter(document__category=category_filter)

        reports = list(qs.order_by("-updated_at")[:200])

        # 작성 진행률 = entries 중 selected_problem_ids 또는 comment 있는 것을 카운트.
        # JSONField __len 필터는 backend별 호환성 issue 있어 Python 루프로 정직하게 산출.
        from .models import MatchupHitReportEntry
        all_entries = list(MatchupHitReportEntry.objects.filter(
            tenant=request.tenant, report_id__in=[r.id for r in reports],
        ).select_related("exam_problem").only(
            "id", "report_id", "selected_problem_ids", "comment",
            "exam_problem__id", "exam_problem__embedding", "exam_problem__image_embedding",
            "exam_problem__text", "exam_problem__meta",
        ))
        curated_by_report: dict = {}
        for e in all_entries:
            if (e.selected_problem_ids or []) or (e.comment or "").strip():
                curated_by_report[e.report_id] = curated_by_report.get(e.report_id, 0) + 1

        # 적중률(hit_rate) 산출 — sim≥0.75인 큐레이션 자료를 1건 이상 보유한 문항 비율.
        # PDF 표지 헤드라인과 동일 정의. list endpoint에서 노출하면 강사 통산 KPI 즉시 가시화.
        # 알고리즘: bulk fetch (selected_problem_ids 합집합) → 메모리 dict로 cosine 계산 → entry별 max sim ≥ 0.75 카운트.
        all_sel_ids: set = set()
        for e in all_entries:
            for pid in (e.selected_problem_ids or []):
                try:
                    all_sel_ids.add(int(pid))
                except (TypeError, ValueError):
                    pass
        sel_meta_by_id: dict = {}
        if all_sel_ids:
            for p in MatchupProblem.objects.filter(
                tenant=request.tenant, id__in=list(all_sel_ids),
            ).only("id", "embedding", "image_embedding", "meta", "text"):
                sel_meta_by_id[p.id] = p

        from .pdf_report import _compute_display_sim, _TYPE_HIT
        hit_count_by_report: dict = {}
        for e in all_entries:
            sel_ids = e.selected_problem_ids or []
            if not sel_ids:
                continue
            ep = e.exam_problem
            for pid in sel_ids:
                cand = sel_meta_by_id.get(int(pid)) if isinstance(pid, int) else None
                if not cand:
                    continue
                sim = _compute_display_sim(ep, cand)
                if sim is not None and sim >= _TYPE_HIT:  # 0.75
                    hit_count_by_report[e.report_id] = hit_count_by_report.get(e.report_id, 0) + 1
                    break  # 문항당 1번만

        rows = []
        total_hit = 0
        total_exam = 0
        for r in reports:
            doc = r.document
            exam_count = doc.problem_count if doc else 0
            curated_count = curated_by_report.get(r.id, 0)
            curated_progress = (curated_count / exam_count * 100.0) if exam_count else 0.0
            hit_count = hit_count_by_report.get(r.id, 0)
            hit_rate = (hit_count / exam_count * 100.0) if exam_count else 0.0
            total_hit += hit_count
            total_exam += exam_count

            author_name = ""
            if r.author_id and r.author is not None:
                author_name = (
                    getattr(r.author, "name", None)
                    or getattr(r.author, "username", "")
                    or getattr(r.author, "email", "")
                ) or ""
                # username 내부 prefix 제거 (t{tid}_ 제거).
                from apps.core.models.user import user_display_username
                if author_name == getattr(r.author, "username", ""):
                    author_name = user_display_username(r.author) or author_name
            elif r.submitted_by_name:
                author_name = r.submitted_by_name

            rows.append({
                "id": r.id,
                "document_id": r.document_id,
                "document_title": doc.title if doc else "",
                "document_category": doc.category if doc else "",
                "author_id": r.author_id,
                "author_name": author_name,
                "title": r.title,
                "status": r.status,
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                "exam_count": exam_count,
                "curated_count": curated_count,
                "curated_progress": round(curated_progress, 1),
                "hit_count": hit_count,
                "hit_rate": round(hit_rate, 1),
                "created_at": r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
            })

        # 통산 적중률 = 모든 보고서의 hit_count 합 / exam_count 합. 강사 1인 누적 KPI.
        avg_hit_rate = (total_hit / total_exam * 100.0) if total_exam else 0.0
        summary = {
            "total": len(rows),
            "submitted": sum(1 for r in rows if r["status"] == "submitted"),
            "drafts": sum(1 for r in rows if r["status"] == "draft"),
            "avg_hit_rate": round(avg_hit_rate, 1),
            "total_hit": total_hit,
            "total_exam": total_exam,
        }
        return JsonResponse({"reports": rows, "summary": summary})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportDraftView(View):
    """GET /api/v1/matchup/documents/<doc_id>/hit-report-draft/

    시험지 doc + 호출자(강사) 기준 적중 보고서 조회. 없으면 자동 draft 생성(author=호출자).
    같은 시험지에 강사 N명이 각자 보고서를 만들 수 있고, 본 응답은 호출자 본인 것만 반환.
    응답에 시험지 problem 목록 + 후보 매치(강사 본인 자료 + 공용 풀) 포함.
    """

    def get(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        # 시험지(test) 또는 exam_sheet만 보고서 작성 가능 (학습자료에 보고서 X)
        meta = doc.meta or {}
        is_test = (
            (meta.get("upload_intent") or "").lower() == "test"
            or (meta.get("document_role") or "").lower() == "exam_sheet"
        )
        if not is_test:
            return JsonResponse(
                {"detail": "보고서는 시험지(test) 자료에서만 작성할 수 있습니다.",
                 "code": "not_test_doc"},
                status=400,
            )

        # 강사 scope: 같은 시험지에 강사별로 별개 보고서. 작성자 본인 보고서를 가져온다.
        # admin/owner가 doc 진입 시: author=user로 자기 보고서 작성. 기존 다른 강사 보고서는 영향 없음.
        report, _ = MatchupHitReport.objects.get_or_create(
            tenant=request.tenant,
            document=doc,
            author=getattr(request, "user", None),
            defaults={"title": doc.title or ""},
        )

        # 시험지 problems
        exam_problems = list(
            doc.problems.order_by("number").only(
                "id", "number", "text", "image_key", "embedding",
            )
        )
        # entry 미리 로드
        entries_by_pid = {
            e.exam_problem_id: e
            for e in report.entries.all()
        }

        # 자동 후보 매치 (find_similar_problems) — 카테고리 격리 적용됨.
        # 큐레이션 보고서 작성자는 자동 top_k=5보다 많은 후보를 보고 직접 골라야 정확도가 올라감
        # (운영 보고: 5개 후보로는 부족 — 학원장 직접 선정 워크플로우 지원).
        from .services import find_similar_problems
        candidate_top_k = 15

        # 병렬 후보 검색 — 시험지 27 문항 × 직렬 ~50s 게이트웨이 컷 회피.
        # 8 worker 동시 vector 검색 → ~6배 단축. 각 검색은 독립 read-only.
        from concurrent.futures import ThreadPoolExecutor

        sim_by_eid: dict = {}
        tenant_id = request.tenant.id
        # 저작권 격리: 보고서 작성자(강사) 본인 자료 + 공용 풀(author=NULL legacy)만 후보.
        # admin/owner가 작성 중인 보고서면 본인 자료 + legacy 풀. 작성자 외 access 시
        # _hit_report_writable이 차단하므로 여기까지 도달하지 않음.
        scope_author_id = getattr(getattr(request, "user", None), "id", None)

        def _fetch_candidates(ep_id: int):
            try:
                return ep_id, find_similar_problems(
                    problem_id=ep_id, tenant_id=tenant_id, top_k=candidate_top_k,
                    author_id=scope_author_id,
                )
            except Exception:
                logger.exception("find_similar_problems failed (problem=%s)", ep_id)
                return ep_id, []

        with ThreadPoolExecutor(max_workers=8) as pool:
            for ep_id, sim_results in pool.map(_fetch_candidates, [ep.id for ep in exam_problems]):
                sim_by_eid[ep_id] = sim_results

        problem_data = []
        all_candidate_ids = set()
        for ep in exam_problems:
            entry = entries_by_pid.get(ep.id)
            cand = []
            sim_results = sim_by_eid.get(ep.id, [])
            for cp, sim in sim_results:
                cand.append({
                    "id": cp.id,
                    "document_id": cp.document_id,
                    "number": cp.number,
                    "text_preview": (cp.text or "")[:120],
                    "similarity": round(sim, 4),
                    "image_key": cp.image_key,
                })
                all_candidate_ids.add(cp.id)

            problem_data.append({
                "id": ep.id,
                "number": ep.number,
                "text_preview": (ep.text or "")[:200],
                "image_key": ep.image_key,
                "candidates": cand,
                "entry": (
                    {
                        "id": entry.id,
                        "selected_problem_ids": entry.selected_problem_ids or [],
                        "comment": entry.comment or "",
                        "order": entry.order,
                    }
                    if entry else None
                ),
            })

        # presigned URL 일괄 — 시험지 problem + 후보 problem
        url_map: dict = {}
        if generate_presigned_get_url_storage:
            for ep in exam_problems:
                if ep.image_key and ep.image_key not in url_map:
                    url_map[ep.image_key] = generate_presigned_get_url_storage(
                        key=ep.image_key, expires_in=3600,
                    )
            # 후보 image_keys
            cand_keys = set()
            for pd in problem_data:
                for c in pd["candidates"]:
                    if c["image_key"]:
                        cand_keys.add(c["image_key"])
            # 사용자 명시 선택 problem (자동 후보에 없을 수도) — 보강
            extra_qs = MatchupProblem.objects.filter(
                tenant=request.tenant,
                id__in=[
                    pid for e in entries_by_pid.values()
                    for pid in (e.selected_problem_ids or [])
                ],
            ).only("id", "image_key", "document_id", "number", "text")
            extra_meta = {p.id: p for p in extra_qs}
            for p in extra_qs:
                if p.image_key:
                    cand_keys.add(p.image_key)
            for k in cand_keys:
                if k not in url_map:
                    url_map[k] = generate_presigned_get_url_storage(
                        key=k, expires_in=3600,
                    )
            for pd in problem_data:
                if pd["image_key"]:
                    pd["image_url"] = url_map.get(pd["image_key"])
                for c in pd["candidates"]:
                    if c["image_key"]:
                        c["image_url"] = url_map.get(c["image_key"])
        else:
            extra_meta = {}

        return JsonResponse({
            "report": MatchupHitReportSerializer(report).data,
            "exam_problems": problem_data,
            "selected_problem_meta": [
                {
                    "id": p.id, "document_id": p.document_id,
                    "number": p.number,
                    "text_preview": (p.text or "")[:120],
                    "image_key": p.image_key,
                    "image_url": url_map.get(p.image_key) if p.image_key else None,
                }
                for p in extra_meta.values()
            ],
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportDetailView(View):
    """
    PATCH  /api/v1/matchup/hit-reports/<id>/         — title/summary 수정
    POST   /api/v1/matchup/hit-reports/<id>/entries/ — 엔트리 일괄 upsert
    POST   /api/v1/matchup/hit-reports/<id>/submit/  — 학원 제출 (status=submitted)
    DELETE /api/v1/matchup/hit-reports/<id>/         — 삭제

    저작권 격리: 모든 조작은 작성자 본인 또는 학원 admin/owner만 가능 (_hit_report_writable).
    """

    def _get(self, request, report_id):
        try:
            return MatchupHitReport.objects.select_related("document").get(
                id=report_id, tenant=request.tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return None

    def patch(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        report = self._get(request, report_id)
        if not report:
            return JsonResponse({"detail": "Not found"}, status=404)
        # 저작권 격리: 작성자 본인 또는 학원 admin/owner만 수정 가능.
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 수정할 수 없습니다."},
                status=403,
            )

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        update_fields = ["updated_at"]
        if "title" in body and isinstance(body["title"], str):
            report.title = body["title"][:255]
            update_fields.append("title")
        if "summary" in body and isinstance(body["summary"], str):
            report.summary = body["summary"]
            update_fields.append("summary")
        report.save(update_fields=update_fields)
        return JsonResponse(MatchupHitReportSerializer(report).data)

    def delete(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        report = self._get(request, report_id)
        if not report:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 삭제할 수 없습니다."},
                status=403,
            )
        report.delete()
        return JsonResponse({"ok": True})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportEntriesUpsertView(View):
    """POST /api/v1/matchup/hit-reports/<id>/entries/

    body: {
      entries: [
        { exam_problem_id: int, selected_problem_ids: [int],
          comment: str, order: int }, ...
      ]
    }
    upsert (report, exam_problem) 단위. 빈 selected + 빈 comment면 삭제.
    """

    def post(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.select_related("document").get(
                id=report_id, tenant=request.tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        # 저작권 격리: 작성자 본인 또는 admin/owner만 entries 수정.
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 수정할 수 없습니다."},
                status=403,
            )

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        entries = body.get("entries")
        if not isinstance(entries, list):
            return JsonResponse({"detail": "entries 배열이 필요합니다."}, status=400)

        # exam_problem_id가 같은 doc의 problem인지 검증 (cross-tenant/cross-doc 차단)
        exam_problem_ids = [int(e.get("exam_problem_id", 0)) for e in entries]
        valid_exam_pids = set(
            MatchupProblem.objects
            .filter(tenant=request.tenant, document=report.document, id__in=exam_problem_ids)
            .values_list("id", flat=True)
        )

        # selected_problem_ids는 보고서 작성자 본인 자료 + 공용 풀(legacy author=NULL)만 허용.
        # 다른 강사 자료를 자기 보고서에 박는 동선 차단 = 저작권 분리.
        # admin/owner는 검증 차원에서 전체 풀 가능 (request.user 본인이 author 본인 케이스 포함).
        all_selected = set()
        for e in entries:
            for pid in (e.get("selected_problem_ids") or []):
                try:
                    all_selected.add(int(pid))
                except (TypeError, ValueError):
                    pass
        from django.db.models import Q
        selected_qs = MatchupProblem.objects.filter(
            tenant=request.tenant, id__in=all_selected,
        )
        if report.author_id and not _is_tenant_admin(request):
            selected_qs = selected_qs.filter(
                Q(document__author_id=report.author_id)
                | Q(document__author__isnull=True)
                | Q(document__isnull=True)  # exam-source problem은 author 무관
            )
        valid_selected = set(selected_qs.values_list("id", flat=True))

        upserted = 0
        deleted = 0
        for e in entries:
            try:
                exam_pid = int(e.get("exam_problem_id"))
            except (TypeError, ValueError):
                continue
            if exam_pid not in valid_exam_pids:
                continue
            sel = [
                pid for pid in (e.get("selected_problem_ids") or [])
                if isinstance(pid, int) and pid in valid_selected
            ]
            comment = (e.get("comment") or "")[:5000]
            try:
                order = int(e.get("order", 0))
            except (TypeError, ValueError):
                order = 0

            if not sel and not comment.strip():
                # 빈 엔트리 → 기존 삭제
                d, _ = MatchupHitReportEntry.objects.filter(
                    report=report, exam_problem_id=exam_pid,
                ).delete()
                deleted += d
                continue

            MatchupHitReportEntry.objects.update_or_create(
                tenant=request.tenant,
                report=report,
                exam_problem_id=exam_pid,
                defaults={
                    "selected_problem_ids": sel,
                    "comment": comment,
                    "order": order,
                },
            )
            upserted += 1

        # report.updated_at 갱신
        report.save(update_fields=["updated_at"])
        return JsonResponse({"upserted": upserted, "deleted": deleted})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportSubmitView(View):
    """POST /api/v1/matchup/hit-reports/<id>/submit/

    상태를 submitted로 전환 + 제출자/제출시각 기록.
    강사가 작성을 마치고 소속 학원에 제출했다는 표식 (KPI 보고).
    """

    def post(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.get(id=report_id, tenant=request.tenant)
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 제출할 수 없습니다."},
                status=403,
            )

        # 중복 발송 방지: status 전이(draft→submitted) 1회만 알림. 이미 submitted 호출 시 알림 skip.
        was_already_submitted = report.status == "submitted"

        from django.utils import timezone
        report.status = "submitted"
        report.submitted_at = timezone.now()
        user = getattr(request, "user", None)
        if user is not None:
            # author FK가 비어있던 legacy report 백필 — 제출 시점에 작성자 식별.
            if not report.author_id:
                report.author = user
            report.submitted_by_id = getattr(user, "id", None)
            full = (
                getattr(user, "name", None)
                or getattr(user, "username", None)
                or getattr(user, "email", "")
            )
            report.submitted_by_name = (full or "")[:100]
        report.save(update_fields=[
            "status", "submitted_at", "submitted_by_id", "submitted_by_name", "author", "updated_at",
        ])

        # B-2: 학원 owner/admin에게 알림톡 (학원별 AutoSendConfig 토글 — 기본 OFF).
        # 첫 제출 1회만 발송 (status 재진입 보호). 실패는 silent — 보고서 제출 자체는 성공.
        if not was_already_submitted:
            try:
                _notify_hit_report_submitted(report, request)
            except Exception:
                logger.exception("HIT_REPORT_NOTIFY_FAILED | report_id=%s", report.id)

        return JsonResponse(MatchupHitReportSerializer(report).data)


def _notify_hit_report_submitted(report, request) -> None:
    """매치업 보고서 학원 제출 시 owner/admin 알림톡 발송.

    정책 (사용자 결정 2026-05-03):
      - AutoSendConfig 토글 — 기본 OFF, 학원이 messaging 설정에서 ON 시에만.
      - 수신자: 해당 tenant의 owner/admin 권한자 모두 (멀티 admin 학원 케이스 대응).
      - 중복 방지: status draft→submitted 전이 시점 1회만 (호출자 측 가드 + 발송 로그).
      - 발송 실패는 silent — 본 endpoint(submit)의 성공/실패와 분리.

    재사용: TYPE_SCORE 템플릿 (메모리 `community_alimtalk` 패턴, 신규 카카오 검수 회피).
    """
    from apps.domains.messaging.selectors import get_auto_send_config
    from apps.domains.messaging.services import enqueue_sms
    from apps.domains.messaging.alimtalk_content_builders import (
        get_solapi_template_id, build_unified_replacements,
    )
    from apps.domains.messaging.policy import is_messaging_disabled
    from apps.core.models import TenantMembership, Tenant

    trigger = "matchup_report_submitted"
    tenant = report.tenant
    tenant_id = tenant.id

    if is_messaging_disabled(tenant_id):
        logger.info("hit_report_notify skipped: tenant %s messaging disabled", tenant_id)
        return

    config = get_auto_send_config(tenant_id, trigger)
    if not config or not config.enabled:
        logger.debug(
            "hit_report_notify skipped: trigger=%s tenant=%s (config disabled or missing)",
            trigger, tenant_id,
        )
        return

    template = config.template
    template_body = (template.body if template else "") or (
        "강사가 매치업 적중 보고서를 제출했습니다.\n"
        "어드민 → 매치업에서 보고서 inbox를 확인해 주세요."
    )

    tenant_name = (tenant.name or "").strip() or "학원"
    site_url = "https://hakwonplus.com"
    if tenant.code:
        site_url = f"https://{tenant.code}.hakwonplus.com"

    author_name = ""
    if report.author_id and report.author is not None:
        from apps.core.models.user import user_display_username
        author_name = (
            getattr(report.author, "name", None)
            or user_display_username(report.author)
            or ""
        )
    if not author_name:
        author_name = report.submitted_by_name or "강사"

    doc = report.document
    doc_title = (doc.title if doc else "") or "시험지"
    doc_category = (doc.category if doc else "") or ""

    # ITEM_LIST 슬롯 매핑 — score 템플릿 재사용 ("강의명"=학교/카테고리, "차시명"=시험지+강사)
    context = {
        "강의명": (doc_category or doc_title)[:30],
        "차시명": f"{doc_title[:20]}  ·  {author_name} 강사"[:30],
    }

    # 수신자 — owner/admin 멀티 (TenantMembership active)
    memberships = list(
        TenantMembership.objects.filter(
            tenant=tenant, is_active=True, role__in=["owner", "admin"],
        ).select_related("user").only(
            "user__id", "user__name", "user__username", "user__phone",
        )
    )
    if not memberships:
        logger.info("hit_report_notify: no owner/admin in tenant %s", tenant_id)
        return

    solapi_tid = get_solapi_template_id(trigger)
    sent_count = 0
    sent_user_ids: list[int] = []
    for m in memberships:
        u = getattr(m, "user", None)
        if not u:
            continue
        phone = (getattr(u, "phone", "") or "").replace("-", "").strip()
        if not phone:
            logger.debug(
                "hit_report_notify: user %s has no phone, skip", getattr(u, "id", "?"),
            )
            continue

        recipient_name = getattr(u, "name", None) or getattr(u, "username", "") or ""

        sms_kwargs = dict(
            tenant_id=tenant_id,
            to=phone,
            text=template_body,
            message_mode="alimtalk",
        )
        if solapi_tid:
            replacements = build_unified_replacements(
                trigger=trigger,
                content_body=template_body,
                context=context,
                tenant_name=tenant_name,
                student_name=recipient_name,  # score 템플릿 수신자 슬롯
                site_url=site_url,
            )
            sms_kwargs["template_id"] = solapi_tid
            sms_kwargs["alimtalk_replacements"] = replacements

        try:
            ok = enqueue_sms(**sms_kwargs)
            if ok:
                sent_count += 1
                sent_user_ids.append(u.id)
        except Exception as e:
            logger.warning(
                "hit_report_notify enqueue failed: report=%s user=%s err=%s",
                report.id, u.id, e,
            )

    # 발송 로그 — meta에 영구 기록 (운영 감사 추적용. 본 컬럼은 신규 추가 없이 jsonb meta 활용 가능하나
    # MatchupHitReport는 meta 필드가 없으므로 logger.info만 남긴다).
    logger.info(
        "HIT_REPORT_NOTIFIED | tenant=%s report=%s author=%s recipients=%d/%d user_ids=%s",
        tenant_id, report.id, report.author_id, sent_count, len(memberships), sent_user_ids,
    )


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportPdfView(View):
    """GET /api/v1/matchup/hit-reports/<id>/curated.pdf

    강사 1인 적중 보고서 PDF — 수업 히스토리 + 학원 KPI + 신뢰자료/홍보물 3중 역할.
    표지(작성 강사 + 적중률 요약) + 각 문항(좌:학생 시험지 / 우:강사 수업자료 + 지도 코멘트).
    """

    def get(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.select_related("document", "author").get(
                id=report_id, tenant=request.tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        # 보고서는 강사 1인의 산출물 — 본인 또는 학원 admin/owner만 PDF 다운로드 가능.
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 다운로드할 수 없습니다."},
                status=403,
            )

        try:
            from .pdf_report import generate_curated_hit_report_pdf
            pdf_bytes = generate_curated_hit_report_pdf(report)
        except Exception:
            logger.exception("curated_hit_report_pdf failed (report=%s)", report.id)
            return JsonResponse({"detail": "PDF 생성 실패"}, status=500)

        from urllib.parse import quote
        title = report.title or report.document.title or f"matchup-hitreport-{report.id}"
        safe_name = quote(title[:80])
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = (
            f"attachment; filename=\"matchup-hitreport-{report.id}.pdf\"; "
            f"filename*=UTF-8''{safe_name}.pdf"
        )
        resp["Cache-Control"] = "private, no-cache"
        return resp


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportZipExportView(View):
    """GET /api/v1/matchup/hit-reports/<id>/share.zip

    카페·블로그 게시용 raw asset 패키지 — 강사가 PDF 그대로 가져다 쓸 수 있게.
      - pages/page_001.png ... page_N.png : 페이지별 PNG (PDF 페이지 1:1 변환)
      - cover.png                          : 표지 이미지 (page_001 alias)
      - summary.md                         : 강사명/학원/시험지/적중률/문항 코멘트 markdown
      - README.txt                         : 카페 게시 가이드

    PDF은 학원 제출용 정식 산출물 / ZIP은 강사가 카페에 자유 게시 시 paste·업로드용.
    외부 공유 link(R-C C-1)는 별개 — 본 endpoint도 staff 인증 필요. zip은 강사가
    수동 다운로드 후 본인 명의로 카페에 게시.
    """

    def get(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.select_related("document", "author").get(
                id=report_id, tenant=request.tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 다운로드할 수 없습니다."},
                status=403,
            )

        try:
            zip_bytes = _build_hit_report_share_zip(report)
        except Exception:
            logger.exception("hit_report_share_zip failed (report=%s)", report.id)
            return JsonResponse({"detail": "ZIP 생성 실패"}, status=500)

        from urllib.parse import quote
        title = report.title or report.document.title or f"matchup-hitreport-{report.id}"
        safe_name = quote(title[:80])
        resp = HttpResponse(zip_bytes, content_type="application/zip")
        resp["Content-Disposition"] = (
            f"attachment; filename=\"matchup-hitreport-{report.id}-share.zip\"; "
            f"filename*=UTF-8''{safe_name}-카페공유.zip"
        )
        resp["Cache-Control"] = "private, no-cache"
        return resp


def _build_hit_report_share_zip(report) -> bytes:
    """PDF → 페이지별 PNG + summary.md + README.txt → in-memory ZIP.

    PyMuPDF로 PDF 페이지 → 200dpi PNG 변환. 이미 PDF 생성 로직(이미지 prefetch +
    레이아웃)을 재사용하므로 ZIP 생성은 PDF 1회 빌드 + 페이지 렌더 비용.
    """
    import io
    import zipfile
    from datetime import datetime

    from .pdf_report import generate_curated_hit_report_pdf, _compute_display_sim
    from .models import MatchupProblem
    from academy.adapters.tools.pymupdf_renderer import PdfDocument

    pdf_bytes = generate_curated_hit_report_pdf(report)

    # PDF → 페이지별 PNG (200 dpi — 카페 업로드 시 화질 충분, 사이즈 적정).
    page_pngs: list[bytes] = []
    pdf_temp = io.BytesIO(pdf_bytes)
    pdf_temp.seek(0)
    import tempfile
    import os
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(pdf_bytes)
        with PdfDocument(tmp_path) as doc_pdf:
            for i in range(doc_pdf.page_count()):
                page_img = doc_pdf.render_page(i, dpi=200)
                buf = io.BytesIO()
                page_img.save(buf, "PNG", optimize=True)
                page_pngs.append(buf.getvalue())
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # summary.md — 카페 본문에 paste 가능한 markdown
    document = report.document
    tenant = document.tenant
    tenant_name = (tenant.name or "").strip() or "학원"

    author_name = ""
    if report.author_id and report.author is not None:
        try:
            from apps.core.models.user import user_display_username
            author_name = (
                getattr(report.author, "name", None)
                or user_display_username(report.author)
                or ""
            ).strip()
        except Exception:
            author_name = ""
    if not author_name:
        author_name = report.submitted_by_name or ""

    issued_at = (
        report.submitted_at.strftime("%Y년 %m월 %d일") if report.submitted_at
        else datetime.now().strftime("%Y년 %m월 %d일")
    )

    # 적중률 산출 — PDF 표지와 동일 정의
    exam_problems = list(
        document.problems.exclude(image_key="").order_by("number")
    )
    entries_by_eid = {e.exam_problem_id: e for e in report.entries.all()}
    all_sel_ids = set()
    for e in entries_by_eid.values():
        for pid in (e.selected_problem_ids or []):
            try:
                all_sel_ids.add(int(pid))
            except (TypeError, ValueError):
                pass
    sel_meta = {}
    if all_sel_ids:
        for p in MatchupProblem.objects.filter(
            tenant=tenant, id__in=list(all_sel_ids),
        ).only("id", "embedding", "image_embedding", "meta", "text", "number", "document_id"):
            sel_meta[p.id] = p

    hit_count = 0
    for ep in exam_problems:
        e = entries_by_eid.get(ep.id)
        sel_ids = (e.selected_problem_ids if e else []) or []
        for pid in sel_ids:
            cand = sel_meta.get(int(pid)) if isinstance(pid, int) else None
            if not cand:
                continue
            sim = _compute_display_sim(ep, cand)
            if sim is not None and sim >= 0.75:
                hit_count += 1
                break
    total_q = len(exam_problems)
    hit_rate = (hit_count / total_q * 100) if total_q else 0.0

    md_lines: list[str] = []
    md_lines.append(f"# {report.title or document.title or '매치업 적중 보고서'}")
    md_lines.append("")
    md_lines.append(f"- **학원**: {tenant_name}")
    if author_name:
        md_lines.append(f"- **강사**: {author_name}")
    md_lines.append(f"- **시험**: {document.title or ''}")
    if document.category:
        md_lines.append(f"- **카테고리**: {document.category}")
    md_lines.append(f"- **발행일**: {issued_at}")
    md_lines.append(f"- **매치업 적중률**: {hit_rate:.1f}%  (전체 {total_q}문항 중 {hit_count}문항이 학원 자료와 75%+ 유사)")
    md_lines.append("")

    if (report.summary or "").strip():
        md_lines.append("## 보고서 요약")
        md_lines.append("")
        md_lines.append(report.summary.strip())
        md_lines.append("")

    md_lines.append("## 문항별 코멘트")
    md_lines.append("")
    for ep in exam_problems:
        e = entries_by_eid.get(ep.id)
        comment = ((e.comment if e else "") or "").strip()
        if not comment:
            continue
        md_lines.append(f"### Q{ep.number}")
        md_lines.append("")
        md_lines.append(comment)
        md_lines.append("")

    md_lines.append("---")
    md_lines.append("")
    md_lines.append(f"_본 보고서는 {tenant_name}의 매치업 적중 분석 결과입니다._")
    summary_md = "\n".join(md_lines).encode("utf-8")

    # README.txt — 사용 가이드
    readme_lines = [
        "매치업 적중 보고서 — 카페/블로그 공유용 패키지",
        "",
        "구성:",
        "  pages/page_001.png  ~  page_NNN.png  : 페이지별 PNG (PDF와 동일 양식)",
        "  cover.png                            : 표지 (page_001 alias)",
        "  summary.md                           : 카페 본문에 paste 가능한 markdown 요약",
        "  README.txt                           : 본 안내 파일",
        "",
        "사용:",
        "  1. summary.md 내용을 카페 글 본문에 복사·붙여넣기",
        "  2. pages/*.png 또는 cover.png을 카페 에디터에 이미지 업로드",
        "     (네이버 카페·블로그 모두 PNG 직접 업로드 지원)",
        "  3. 본 자료는 강사 본인 명의로 자유롭게 게시 가능",
        "",
        "주의:",
        "  - 본 ZIP은 작성 강사 또는 학원 owner/admin만 다운로드 가능",
        "  - 학원의 다른 강사 자료가 포함되었을 수 있으니 게시 전 확인",
    ]
    readme_txt = "\n".join(readme_lines).encode("utf-8")

    # ZIP 패키징
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, png in enumerate(page_pngs, start=1):
            zf.writestr(f"pages/page_{i:03d}.png", png)
        if page_pngs:
            zf.writestr("cover.png", page_pngs[0])  # 페이지 1 alias = 표지
        zf.writestr("summary.md", summary_md)
        zf.writestr("README.txt", readme_txt)

    return zip_buf.getvalue()


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentHitReportPdfView(View):
    """폐기됨 (deprecated). 자동 PDF는 큐레이션 보고서로 대체.

    URL은 backward compat 위해 유지하되 410 Gone 반환.
    프론트엔드 버튼은 이미 제거됨.
    """

    def get(self, request, doc_id):
        return JsonResponse(
            {"detail": "자동 적중 PDF는 폐기되었습니다. 큐레이션 보고서를 사용하세요."},
            status=410,
        )


