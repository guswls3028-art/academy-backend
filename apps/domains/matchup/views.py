# PATH: apps/domains/matchup/views.py
# 매치업 API views — 문서 CRUD + 문제 조회 + 유사 검색

from __future__ import annotations

import logging

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import MatchupDocument, MatchupProblem
from .serializers import (
    MatchupDocumentSerializer,
    MatchupDocumentUpdateSerializer,
    MatchupProblemSerializer,
    SimilarProblemSerializer,
)
from .services import (
    find_similar_problems,
    delete_document_with_r2,
    retry_document,
    promote_inventory_to_matchup,
    ensure_matchup_upload_folder,
)

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
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
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
                {"detail": "파일 크기가 50MB를 초과합니다."},
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
        subject = request.POST.get("subject", "")
        grade_level = request.POST.get("grade_level", "")

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

        # 2) 즉시 승격 + dispatch
        doc = promote_inventory_to_matchup(
            inv_file,
            title=title,
            subject=subject,
            grade_level=grade_level,
        )

        data = MatchupDocumentSerializer(doc).data
        return JsonResponse(data, status=201)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentPromoteFromInventoryView(View):
    """POST /api/v1/matchup/documents/promote/

    body: { inventory_file_id: int, title?: str, subject?: str, grade_level?: str }
    저장소 admin scope 파일을 매치업 분석 대상으로 승격.
    """

    def post(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        import json
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

        # 학생 scope 파일은 승격 불가 (매치업은 staff 전용 도구)
        if inv_file.scope != "admin":
            return JsonResponse(
                {"detail": "선생님 저장소(admin scope) 파일만 매치업으로 승격할 수 있습니다."},
                status=400,
            )

        # 매치업 가능한 형식 체크
        if inv_file.content_type not in ALLOWED_CONTENT_TYPES:
            return JsonResponse(
                {"detail": f"매치업은 PDF/PNG/JPG만 지원합니다. (현재: {inv_file.content_type})"},
                status=400,
            )

        # 중복 승격 차단
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

        # 테넌트당 최대 문서 수 체크
        doc_count = MatchupDocument.objects.filter(tenant=request.tenant).count()
        if doc_count >= MAX_DOCUMENTS_PER_TENANT:
            return JsonResponse(
                {"detail": f"테넌트당 최대 {MAX_DOCUMENTS_PER_TENANT}개 문서까지 업로드 가능합니다."},
                status=400,
            )

        title = (body.get("title") or "").strip() or inv_file.display_name
        subject = body.get("subject", "")
        grade_level = body.get("grade_level", "")

        doc = promote_inventory_to_matchup(
            inv_file,
            title=title,
            subject=subject,
            grade_level=grade_level,
        )

        data = MatchupDocumentSerializer(doc).data
        return JsonResponse(data, status=201)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentListView(View):
    """GET /api/v1/matchup/documents/"""

    def get(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        docs = MatchupDocument.objects.filter(tenant=request.tenant)
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
        for field in ("title", "subject", "grade_level"):
            if field in ser.validated_data:
                setattr(doc, field, ser.validated_data[field])
                update_fields.append(field)

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
    """GET /api/v1/matchup/problems/<id>/"""

    def get(self, request, problem_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        try:
            problem = MatchupProblem.objects.get(id=problem_id, tenant=request.tenant)
        except MatchupProblem.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        data = MatchupProblemSerializer(problem).data

        # 이미지 presigned URL 추가
        if problem.image_key and generate_presigned_get_url_storage:
            data["image_url"] = generate_presigned_get_url_storage(
                key=problem.image_key, expires_in=3600
            )

        return JsonResponse(data)


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
