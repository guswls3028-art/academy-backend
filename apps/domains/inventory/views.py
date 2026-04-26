# PATH: apps/domains/inventory/views.py
# 저장소 API — R2 업로드 후 DB 메타데이터, 비어있지 않은 폴더 삭제 방지

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings

from apps.core.authentication import TokenVersionJWTAuthentication as JWTAuthentication

from apps.core.models import Program
from .models import InventoryFolder, InventoryFile
from .r2_path import build_r2_key, safe_filename, folder_path_string
from academy.adapters.db.django import repositories_inventory as inv_repo
from .services import move_file as do_move_file, move_folder as do_move_folder

# R2 Storage 버킷 (인벤토리 전용)
try:
    from apps.infrastructure.storage.r2 import (
        upload_fileobj_to_r2_storage,
        generate_presigned_get_url_storage,
        copy_object_r2_storage,
        delete_object_r2_storage,
    )
except ImportError:
    upload_fileobj_to_r2_storage = None
    generate_presigned_get_url_storage = None
    copy_object_r2_storage = None
    delete_object_r2_storage = None

QUOTA_BYTES = {"standard": 10 * 1024**3, "pro": 50 * 1024**3, "max": 200 * 1024**3}


def _tenant_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return JsonResponse({"detail": "Tenant required"}, status=400)
        return view_func(request, *args, **kwargs)
    return wrapped


def _jwt_required(view_func):
    """JWT 인증 필수. 미인증 시 401 (저장소 API는 로그인 사용자만 허용)."""
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
    """요청 사용자가 테넌트의 스태프(owner/admin/teacher/assistant)인지 확인."""
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


def _check_scope_permission(request, scope=None):
    """admin scope 접근 시 스태프 권한 필수. 학생은 자기 scope만 접근."""
    if scope is None:
        import json
        try:
            body = json.loads(request.body)
        except Exception:
            body = {}
        scope = (body.get("scope") or request.GET.get("scope") or "admin").lower()
    if scope == "admin" and not _is_tenant_staff(request):
        return JsonResponse({"detail": "관리자 권한이 필요합니다."}, status=403)
    if scope == "student" and not _is_tenant_staff(request):
        # 학생은 자기 ps_number만 접근 가능
        student_profile = getattr(request.user, "student_profile", None)
        if not student_profile:
            return JsonResponse({"detail": "학생 정보가 없습니다."}, status=403)
        student_ps = (request.GET.get("student_ps") or "").strip()
        if not student_ps:
            import json
            try:
                body = json.loads(request.body)
            except Exception:
                body = {}
            student_ps = (body.get("student_ps") or "").strip()
        if student_ps and student_ps != student_profile.ps_number:
            return JsonResponse({"detail": "다른 학생의 자료에 접근할 수 없습니다."}, status=403)
    return None  # OK


@method_decorator(csrf_exempt, name="dispatch")
class QuotaView(View):
    """GET /storage/quota/ — 테넌트 사용량 및 플랜 한도."""

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def get(self, request):
        tenant = request.tenant
        try:
            program = Program.ensure_for_tenant(tenant=tenant)
            plan = (program.plan or "pro").lower()
        except Exception:
            plan = "pro"
        try:
            limit = QUOTA_BYTES.get(plan, QUOTA_BYTES["pro"])
            used = inv_repo.inventory_file_aggregate_size(tenant)
            return JsonResponse({
                "usedBytes": used,
                "limitBytes": limit,
                "plan": plan,
            })
        except Exception as e:
            return JsonResponse(
                {"detail": str(e) if settings.DEBUG else "Internal Server Error"},
                status=500,
            )


@method_decorator(csrf_exempt, name="dispatch")
class InventoryListView(View):
    """GET /storage/inventory/?scope=admin|student&student_ps=... — 폴더/파일 목록 (전체, 클라이언트에서 필터)."""

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def get(self, request):
        scope = (request.GET.get("scope") or "admin").lower()
        if scope not in ("admin", "student"):
            return JsonResponse({"detail": "Invalid scope"}, status=400)
        perm_err = _check_scope_permission(request, scope)
        if perm_err:
            return perm_err
        student_ps = (request.GET.get("student_ps") or "").strip()
        if scope == "student" and not student_ps:
            return JsonResponse({"detail": "student_ps required for student scope"}, status=400)

        try:
            tenant = request.tenant
            student_ps_arg = student_ps if scope == "student" else None
            qs_folders = inv_repo.inventory_folder_filter(tenant, scope, student_ps_arg)
            qs_files = inv_repo.inventory_file_filter(tenant, scope, student_ps_arg)

            # 매치업 승격된 파일 ID set (admin scope에만 의미 있음)
            promoted_map: dict[int, dict] = {}
            if scope == "admin":
                from apps.domains.matchup.models import MatchupDocument
                file_ids = [f.id for f in qs_files]
                if file_ids:
                    docs = MatchupDocument.objects.filter(
                        tenant=tenant, inventory_file_id__in=file_ids,
                    ).values("id", "inventory_file_id", "status", "problem_count")
                    promoted_map = {d["inventory_file_id"]: d for d in docs}

            folders = [
                {"id": str(f.id), "name": f.name, "parentId": str(f.parent_id) if f.parent_id else None}
                for f in qs_folders
            ]
            files = []
            for f in qs_files:
                row = {
                    "id": str(f.id),
                    "name": f.original_name,
                    "displayName": f.display_name,
                    "description": f.description or "",
                    "icon": f.icon or "file-text",
                    "folderId": str(f.folder_id) if f.folder_id else None,
                    "sizeBytes": f.size_bytes,
                    "r2Key": f.r2_key,
                    "contentType": f.content_type or "",
                    "createdAt": f.created_at.isoformat() if f.created_at else "",
                }
                doc_info = promoted_map.get(f.id)
                if doc_info:
                    row["matchup"] = {
                        "documentId": doc_info["id"],
                        "status": doc_info["status"],
                        "problemCount": doc_info["problem_count"],
                    }
                files.append(row)
            return JsonResponse({"folders": folders, "files": files})
        except Exception as e:
            return JsonResponse(
                {"detail": str(e) if settings.DEBUG else "Internal Server Error"},
                status=500,
            )


@method_decorator(csrf_exempt, name="dispatch")
class FolderCreateView(View):
    """
    POST /storage/inventory/folders/ — 폴더 생성.

    필수: tenant(요청 host로 해석), name
    선택: scope(기본 admin), student_ps(scope=student일 때 필수), parent_id(숫자 또는 null/빈값=루트)

    404 가능 원인:
    - 미들웨어: 요청 host가 TenantDomain에 없음 (새 테넌트면 해당 host 등록 필요)
    - parent_id에 해당하는 폴더가 해당 테넌트에 없음
    500: create 실패 시 DEBUG면 상세 메시지 반환
    """

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def post(self, request):
        import json
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)
        scope = (body.get("scope") or "admin").lower()
        student_ps = (body.get("student_ps") or "").strip()
        parent_id = body.get("parent_id")
        name = (body.get("name") or "").strip()
        if not name:
            return JsonResponse({"detail": "name required"}, status=400)
        if scope == "student" and not student_ps:
            return JsonResponse({"detail": "student_ps required for student scope"}, status=400)

        perm_err = _check_scope_permission(request, scope)
        if perm_err:
            return perm_err

        tenant = request.tenant
        parent = None
        pid = None
        if parent_id is not None and parent_id != "":
            try:
                pid = int(parent_id)
            except (TypeError, ValueError):
                return JsonResponse({"detail": "parent_id must be a number"}, status=400)
            parent = inv_repo.inventory_folder_get(tenant, pid)
            if not parent:
                return JsonResponse({"detail": "Parent folder not found"}, status=404)

        try:
            folder = inv_repo.inventory_folder_create(
                tenant, pid if parent_id not in (None, "") else None, name, scope, student_ps or "",
            )
        except Exception as e:
            return JsonResponse(
                {"detail": str(e) if settings.DEBUG else "Failed to create folder"},
                status=500,
            )
        return JsonResponse({
            "id": str(folder.id),
            "name": folder.name,
            "parentId": str(folder.parent_id) if folder.parent_id else None,
        })


@method_decorator(csrf_exempt, name="dispatch")
class FileUploadView(View):
    """POST /storage/inventory/upload/ — R2 업로드 후 DB 저장. 플랜 한도 체크."""

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def post(self, request):
        scope = (request.POST.get("scope") or "admin").lower()
        student_ps = (request.POST.get("student_ps") or "").strip()

        perm_err = _check_scope_permission(request, scope)
        if perm_err:
            return perm_err

        folder_id = request.POST.get("folder_id")
        display_name = (request.POST.get("display_name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        icon = (request.POST.get("icon") or "file-text").strip()
        file_obj = request.FILES.get("file")
        if not file_obj:
            return JsonResponse({"detail": "file required"}, status=400)

        # 파일 크기 제한: 100MB
        MAX_FILE_SIZE = 100 * 1024 * 1024
        if file_obj.size > MAX_FILE_SIZE:
            return JsonResponse({"detail": "파일 크기가 100MB를 초과합니다."}, status=400)

        # 파일 타입 화이트리스트
        ALLOWED_CONTENT_TYPES = {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/plain",
            "application/zip",
        }
        ALLOWED_TYPE_PREFIXES = ("image/", "video/")
        ct = getattr(file_obj, "content_type", "") or ""
        if ct not in ALLOWED_CONTENT_TYPES and not any(ct.startswith(p) for p in ALLOWED_TYPE_PREFIXES):
            return JsonResponse(
                {"detail": "허용되지 않는 파일 형식입니다."},
                status=400,
            )

        if scope == "student" and not student_ps:
            return JsonResponse({"detail": "student_ps required for student scope"}, status=400)

        tenant = request.tenant
        # Quota
        try:
            program = Program.ensure_for_tenant(tenant=tenant)
            plan = (program.plan or "pro").lower()
        except Exception:
            plan = "pro"
        limit = QUOTA_BYTES.get(plan, QUOTA_BYTES["pro"])
        if plan == "standard":
            return JsonResponse({"detail": "인벤토리 기능을 사용할 수 없는 플랜입니다.", "code": "plan_standard"}, status=403)
        used = inv_repo.inventory_file_aggregate_size(tenant)
        if used + file_obj.size > limit:
            return JsonResponse({"detail": "용량 한도를 초과했습니다. 플랜 업그레이드가 필요합니다.", "code": "quota_exceeded"}, status=403)

        folder = None
        folder_path = ""
        if folder_id:
            folder = inv_repo.inventory_folder_get(tenant, int(folder_id))
            if folder:
                path_parts = []
                p = folder
                while p:
                    path_parts.append(p.name)
                    p = p.parent
                folder_path = folder_path_string(reversed(path_parts))

        safe_name = safe_filename(file_obj.name)
        r2_key = build_r2_key(
            tenant_id=tenant.id,
            scope=scope,
            student_ps=student_ps,
            folder_path=folder_path,
            file_name=safe_name,
        )

        if upload_fileobj_to_r2_storage:
            try:
                upload_fileobj_to_r2_storage(
                    fileobj=file_obj,
                    key=r2_key,
                    content_type=file_obj.content_type or "application/octet-stream",
                )
            except Exception as e:
                return JsonResponse({"detail": f"R2 upload failed: {e}"}, status=502)

        inv_file = inv_repo.inventory_file_create(
            tenant=tenant,
            scope=scope,
            student_ps=student_ps,
            folder=folder,
            display_name=display_name or file_obj.name,
            description=description,
            icon=icon,
            r2_key=r2_key,
            original_name=file_obj.name,
            size_bytes=file_obj.size,
            content_type=file_obj.content_type or "application/octet-stream",
        )

        # 매치업 승격 토글 — admin scope + 매치업 가능 형식만
        promote_flag = (request.POST.get("promote_to_matchup") or "").strip().lower()
        promote = promote_flag in ("1", "true", "yes")
        matchup_doc_id = None
        matchup_promote_failed = False
        matchup_error = ""
        if promote:
            if scope != "admin":
                return JsonResponse(
                    {"detail": "선생님 저장소 파일만 매치업으로 승격할 수 있습니다."},
                    status=400,
                )
            matchup_allowed = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}
            if (file_obj.content_type or "") not in matchup_allowed:
                return JsonResponse(
                    {"detail": "매치업은 PDF/PNG/JPG만 지원합니다."},
                    status=400,
                )
            try:
                from apps.domains.matchup.services import promote_inventory_to_matchup
                doc = promote_inventory_to_matchup(
                    inv_file,
                    title=inv_file.display_name,
                    subject=(request.POST.get("subject") or ""),
                    grade_level=(request.POST.get("grade_level") or ""),
                )
                matchup_doc_id = doc.id
                matchup_ai_job_id = doc.ai_job_id or ""
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception(
                    "Promote to matchup failed for inventory_file %s", inv_file.id
                )
                # InventoryFile은 살려두고 부분 실패 명시
                matchup_doc_id = None
                matchup_ai_job_id = ""
                matchup_promote_failed = True
                matchup_error = str(e)[:200]
        else:
            matchup_ai_job_id = ""

        payload = {
            "id": str(inv_file.id),
            "name": inv_file.original_name,
            "displayName": inv_file.display_name,
            "description": inv_file.description,
            "icon": inv_file.icon,
            "folderId": str(inv_file.folder_id) if inv_file.folder_id else None,
            "sizeBytes": inv_file.size_bytes,
            "r2Key": inv_file.r2_key,
            "contentType": inv_file.content_type,
            "createdAt": inv_file.created_at.isoformat() if inv_file.created_at else "",
        }
        if matchup_doc_id is not None:
            payload["matchupDocumentId"] = matchup_doc_id
            if matchup_ai_job_id:
                payload["matchupAiJobId"] = matchup_ai_job_id
        if matchup_promote_failed:
            payload["matchupPromoteFailed"] = True
            payload["matchupError"] = matchup_error
        return JsonResponse(payload)


@method_decorator(csrf_exempt, name="dispatch")
class FolderDeleteView(View):
    """DELETE /storage/inventory/folders/:id/ — 비어있을 때만 삭제.
    PATCH — 폴더 이름 변경."""

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def delete(self, request, folder_id):
        tenant = request.tenant
        scope = (request.GET.get("scope") or "admin").lower()
        student_ps = (request.GET.get("student_ps") or "").strip()

        # 🔐 scope 권한 검증: 학생은 admin scope 접근 불가
        perm_err = _check_scope_permission(request, scope)
        if perm_err:
            return perm_err

        folder = inv_repo.inventory_folder_get(tenant, folder_id)
        if not folder:
            return JsonResponse({"detail": "Not found"}, status=404)
        if folder.scope != scope or (scope == "student" and folder.student_ps != student_ps):
            return JsonResponse({"detail": "Forbidden"}, status=403)

        if inv_repo.inventory_folder_has_children(tenant, folder):
            return JsonResponse({"detail": "비어있지 않은 폴더는 지울 수 없습니다. 먼저 하위 파일·폴더를 비우거나 삭제하세요.", "code": "folder_not_empty"}, status=400)
        if inv_repo.inventory_folder_has_files(tenant, folder):
            return JsonResponse({"detail": "비어있지 않은 폴더는 지울 수 없습니다. 먼저 하위 파일·폴더를 비우거나 삭제하세요.", "code": "folder_not_empty"}, status=400)

        folder.delete()
        return JsonResponse({}, status=204)

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def patch(self, request, folder_id):
        import json
        tenant = request.tenant
        scope = (request.GET.get("scope") or "admin").lower()
        student_ps = (request.GET.get("student_ps") or "").strip()

        # 🔐 scope 권한 검증
        perm_err = _check_scope_permission(request, scope)
        if perm_err:
            return perm_err

        folder = inv_repo.inventory_folder_get(tenant, folder_id)
        if not folder:
            return JsonResponse({"detail": "Not found"}, status=404)
        if folder.scope != scope or (scope == "student" and folder.student_ps != student_ps):
            return JsonResponse({"detail": "Forbidden"}, status=403)

        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        name = (body.get("name") or "").strip()
        if not name:
            return JsonResponse({"detail": "이름을 입력해주세요."}, status=400)
        if len(name) > 100:
            return JsonResponse({"detail": "이름은 100자 이하로 입력해주세요."}, status=400)

        folder.name = name
        folder.save(update_fields=["name", "updated_at"])
        return JsonResponse({"id": str(folder.id), "name": folder.name})


@method_decorator(csrf_exempt, name="dispatch")
class FileDeleteView(View):
    """DELETE /storage/inventory/files/:id/ — DB 삭제 후 R2 객체 삭제.
    PATCH — 파일 표시명/설명 변경."""

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def delete(self, request, file_id):
        tenant = request.tenant
        scope = (request.GET.get("scope") or "admin").lower()
        student_ps = (request.GET.get("student_ps") or "").strip()

        # 🔐 scope 권한 검증: 학생은 admin scope 파일 삭제 불가
        perm_err = _check_scope_permission(request, scope)
        if perm_err:
            return perm_err

        inv_file = inv_repo.inventory_file_get(tenant, file_id)
        if not inv_file:
            return JsonResponse({"detail": "Not found"}, status=404)
        if inv_file.scope != scope or (scope == "student" and inv_file.student_ps != student_ps):
            return JsonResponse({"detail": "Forbidden"}, status=403)

        r2_key = inv_file.r2_key

        # 🔐 매치업 problem 이미지 R2 cleanup (cascade로 doc/problem 삭제 전)
        # InventoryFile cascade → MatchupDocument → MatchupProblem만 일어남.
        # MatchupProblem.image_key R2 객체는 누가 안 지움 → orphan 방지.
        try:
            matchup_doc = getattr(inv_file, "matchup_document", None)
        except Exception:
            matchup_doc = None
        if matchup_doc is not None:
            try:
                from apps.domains.matchup.services import cleanup_matchup_problem_images
                cleanup_matchup_problem_images(matchup_doc)
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "matchup problem images cleanup failed for inv_file %s", inv_file.id,
                    exc_info=True,
                )

        inv_file.delete()  # CASCADE: MatchupDocument → MatchupProblem 함께 삭제

        # 🔐 원본 R2 객체 삭제 (스토리지 누수 방지)
        if r2_key and delete_object_r2_storage:
            try:
                delete_object_r2_storage(key=r2_key)
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to delete R2 object: %s", r2_key, exc_info=True
                )

        return JsonResponse({}, status=204)

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def patch(self, request, file_id):
        import json
        tenant = request.tenant
        scope = (request.GET.get("scope") or "admin").lower()
        student_ps = (request.GET.get("student_ps") or "").strip()

        # 🔐 scope 권한 검증
        perm_err = _check_scope_permission(request, scope)
        if perm_err:
            return perm_err

        inv_file = inv_repo.inventory_file_get(tenant, file_id)
        if not inv_file:
            return JsonResponse({"detail": "Not found"}, status=404)
        if inv_file.scope != scope or (scope == "student" and inv_file.student_ps != student_ps):
            return JsonResponse({"detail": "Forbidden"}, status=403)

        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        fields = []
        if "displayName" in body:
            name = (body["displayName"] or "").strip()
            if not name:
                return JsonResponse({"detail": "이름을 입력해주세요."}, status=400)
            if len(name) > 200:
                return JsonResponse({"detail": "이름은 200자 이하로 입력해주세요."}, status=400)
            inv_file.display_name = name
            fields.append("display_name")
        if "description" in body:
            inv_file.description = (body["description"] or "").strip()[:500]
            fields.append("description")

        if not fields:
            return JsonResponse({"detail": "변경할 내용이 없습니다."}, status=400)

        fields.append("updated_at")
        inv_file.save(update_fields=fields)
        return JsonResponse({
            "id": str(inv_file.id),
            "displayName": inv_file.display_name,
            "description": inv_file.description,
        })


@method_decorator(csrf_exempt, name="dispatch")
class PresignView(View):
    """POST /storage/inventory/presign/ — R2 presigned GET URL."""

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def post(self, request):
        import json
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)
        r2_key = body.get("r2_key")
        expires_in = min(int(body.get("expires_in") or 3600), 3600)  # cap at 1 hour
        if not r2_key:
            return JsonResponse({"detail": "r2_key required"}, status=400)
        # 🔐 tenant isolation: R2 key must belong to the requesting tenant
        tenant = request.tenant
        expected_prefix = f"tenants/{tenant.id}/"
        if not r2_key.startswith(expected_prefix):
            return JsonResponse(
                {"detail": "Access denied: r2_key does not belong to this tenant"},
                status=403,
            )
        if not generate_presigned_get_url_storage:
            return JsonResponse({"url": ""}, status=200)
        url = generate_presigned_get_url_storage(key=r2_key, expires_in=expires_in)
        return JsonResponse({"url": url})


@method_decorator(csrf_exempt, name="dispatch")
class MoveView(View):
    """
    POST /storage/inventory/move/
    Body: { type: "file"|"folder", source_id: str, target_folder_id: str|null, on_duplicate?: "overwrite"|"rename" }
    Copy & Delete 방식: R2 복사 성공 → DB 업데이트 → R2 원본 삭제. 실패 시 원본 삭제 안 함.
    """

    @method_decorator(_tenant_required)
    @method_decorator(_jwt_required)
    def post(self, request):
        import json
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)
        move_type = (body.get("type") or "file").lower()
        source_id = body.get("source_id")
        target_folder_id = body.get("target_folder_id")
        on_duplicate_raw = body.get("on_duplicate")
        on_duplicate = (on_duplicate_raw.strip().lower() if on_duplicate_raw and str(on_duplicate_raw).strip() else None)
        if move_type not in ("file", "folder") or not source_id:
            return JsonResponse({"detail": "type and source_id required"}, status=400)
        scope = (body.get("scope") or request.GET.get("scope") or "admin").lower()
        student_ps = (body.get("student_ps") or request.GET.get("student_ps") or "").strip()
        if scope == "student" and not student_ps:
            return JsonResponse({"detail": "student_ps required for student scope"}, status=400)

        # 🔐 scope 권한 검증: 학생은 admin scope 이동 불가
        perm_err = _check_scope_permission(request, scope)
        if perm_err:
            return perm_err

        tenant = request.tenant
        try:
            sid = int(source_id)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "Invalid source_id"}, status=400)
        tid = None
        if target_folder_id is not None and target_folder_id != "":
            try:
                tid = int(target_folder_id)
            except (TypeError, ValueError):
                return JsonResponse({"detail": "Invalid target_folder_id"}, status=400)

        if move_type == "file":
            result = do_move_file(
                tenant=tenant,
                scope=scope,
                student_ps=student_ps,
                source_file_id=sid,
                target_folder_id=tid,
                on_duplicate=on_duplicate,
            )
        else:
            result = do_move_folder(
                tenant=tenant,
                scope=scope,
                student_ps=student_ps,
                source_folder_id=sid,
                target_folder_id=tid,
                on_duplicate=on_duplicate,
            )

        if not result.get("ok"):
            status = result.get("status", 400)
            payload = {"detail": result.get("detail", "Move failed")}
            if result.get("code"):
                payload["code"] = result["code"]
            if result.get("existing_name") is not None:
                payload["existing_name"] = result["existing_name"]
            return JsonResponse(payload, status=status)
        return JsonResponse({"ok": True})
