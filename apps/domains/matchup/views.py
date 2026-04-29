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
    promote_inventory_to_matchup,
    ensure_matchup_upload_folder,
    manually_crop_problem,
    paste_image_as_problem,
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
        upload_intent = (request.POST.get("intent", "reference") or "reference").strip().lower()
        if upload_intent not in ("reference", "test"):
            upload_intent = "reference"

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

        # 2) 즉시 승격 + dispatch (intent를 promote에 전달 → meta + payload 양쪽에 기록)
        doc = promote_inventory_to_matchup(
            inv_file,
            title=title,
            category=category,
            subject=subject,
            grade_level=grade_level,
            upload_intent=upload_intent,
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
        upload_intent = (body.get("intent", "reference") or "reference").strip().lower()
        if upload_intent not in ("reference", "test"):
            upload_intent = "reference"

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
        if "intent" in ser.validated_data:
            intent = ser.validated_data["intent"]
            meta = dict(doc.meta or {})
            meta["upload_intent"] = intent
            meta["document_role"] = "exam_sheet" if intent == "test" else "reference_material"
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

        results = find_similar_problems(
            problem_id=problem_id,
            tenant_id=request.tenant.id,
            top_k=top_k,
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


# ── Curated Hit Report (사람이 큐레이션한 보고서) ────────────────

@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportDraftView(View):
    """GET /api/v1/matchup/documents/<doc_id>/hit-report-draft/

    시험지 doc 기준 큐레이션 보고서 조회. 없으면 자동 draft 생성.
    응답에 시험지 problem 목록 + 후보 매치(자동 top_k) 포함 → 프론트가 한 번에 그리기.
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

        report, _ = MatchupHitReport.objects.get_or_create(
            tenant=request.tenant, document=doc,
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

        # 자동 후보 매치 (find_similar_problems) — 카테고리 격리 적용됨
        from .services import find_similar_problems
        candidate_top_k = 5

        problem_data = []
        all_candidate_ids = set()
        for ep in exam_problems:
            entry = entries_by_pid.get(ep.id)
            cand = []
            try:
                sim_results = find_similar_problems(
                    problem_id=ep.id, tenant_id=request.tenant.id, top_k=candidate_top_k,
                )
            except Exception:
                logger.exception("find_similar_problems failed (problem=%s)", ep.id)
                sim_results = []
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
    POST   /api/v1/matchup/hit-reports/<id>/submit/  — 제출 (status=submitted)
    DELETE /api/v1/matchup/hit-reports/<id>/         — 삭제 (drag of caution: 관리자만)
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

        # selected_problem_ids는 같은 tenant 안의 problem이어야 함
        all_selected = set()
        for e in entries:
            for pid in (e.get("selected_problem_ids") or []):
                try:
                    all_selected.add(int(pid))
                except (TypeError, ValueError):
                    pass
        valid_selected = set(
            MatchupProblem.objects
            .filter(tenant=request.tenant, id__in=all_selected)
            .values_list("id", flat=True)
        )

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
    실장이 작성을 마치고 선생/학원장에게 제출했다는 표식.
    """

    def post(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.get(id=report_id, tenant=request.tenant)
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        from django.utils import timezone
        report.status = "submitted"
        report.submitted_at = timezone.now()
        user = getattr(request, "user", None)
        if user is not None:
            report.submitted_by_id = getattr(user, "id", None)
            full = (
                getattr(user, "name", None)
                or getattr(user, "username", None)
                or getattr(user, "email", "")
            )
            report.submitted_by_name = (full or "")[:100]
        report.save(update_fields=[
            "status", "submitted_at", "submitted_by_id", "submitted_by_name", "updated_at",
        ])
        return JsonResponse(MatchupHitReportSerializer(report).data)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportPdfView(View):
    """GET /api/v1/matchup/hit-reports/<id>/curated.pdf

    사람이 큐레이션한 보고서 PDF. 표지(요약) + 각 문항(좌:시험지 / 우:선택자료N + 코멘트).
    """

    def get(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.select_related("document").get(
                id=report_id, tenant=request.tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

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
class DocumentHitReportPdfView(View):
    """GET /api/v1/matchup/documents/<id>/hit-report.pdf

    시험지 doc 기준 적중률 PDF 보고서. 학원이 학생/학부모/네이버 카페에
    공유하는 마케팅 보고서. 각 문항별 좌(시험지) | 우(학원 자료) 비교 +
    유사도(%) + 학원 브랜딩.

    Query: ?threshold=0.85 (적중 기준 임계값, 기본 0.85)
    """

    def get(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            threshold = float(request.GET.get("threshold", "0.85"))
            threshold = max(0.5, min(0.99, threshold))
        except ValueError:
            threshold = 0.85

        try:
            from .pdf_report import generate_matchup_hit_report_pdf
            pdf_bytes = generate_matchup_hit_report_pdf(doc, hit_threshold=threshold)
        except Exception:
            logger.exception("matchup_hit_report_pdf failed (doc=%s)", doc.id)
            return JsonResponse({"detail": "PDF 생성 실패"}, status=500)

        # 파일명 — 한글 보존 (RFC 5987 인코딩)
        from urllib.parse import quote
        safe_name = quote((doc.title or f"matchup-{doc.id}")[:80])
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = (
            f"attachment; filename=\"matchup-{doc.id}.pdf\"; "
            f"filename*=UTF-8''{safe_name}.pdf"
        )
        resp["Cache-Control"] = "private, no-cache"
        return resp
