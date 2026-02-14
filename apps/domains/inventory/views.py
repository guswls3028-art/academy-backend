# PATH: apps/domains/inventory/views.py
# 저장소 API — R2 업로드 후 DB 메타데이터, 비어있지 않은 폴더 삭제 방지

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from django.db.models import Sum

from apps.core.models import Program
from .models import InventoryFolder, InventoryFile
from .r2_path import build_r2_key, safe_filename, folder_path_string

# R2 버킷은 settings.R2_AI_BUCKET 사용 (기존 인프라)
try:
    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2, generate_presigned_get_url
except ImportError:
    upload_fileobj_to_r2 = None
    generate_presigned_get_url = None

QUOTA_BYTES = {"lite": 0, "basic": 10 * 1024**3, "premium": 200 * 1024**3}


def _tenant_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return JsonResponse({"detail": "Tenant required"}, status=400)
        return view_func(request, *args, **kwargs)
    return wrapped


class QuotaView(View):
    """GET /storage/quota/ — 테넌트 사용량 및 플랜 한도."""

    @method_decorator(_tenant_required)
    def get(self, request):
        tenant = request.tenant
        try:
            program = Program.ensure_for_tenant(tenant=tenant)
            plan = (program.plan or "basic").lower()
        except Exception:
            plan = "basic"
        limit = QUOTA_BYTES.get(plan, QUOTA_BYTES["basic"])
        used = InventoryFile.objects.filter(tenant=tenant).aggregate(s=Sum("size_bytes"))["s"] or 0
        return JsonResponse({
            "usedBytes": used,
            "limitBytes": limit,
            "plan": plan,
        })


class InventoryListView(View):
    """GET /storage/inventory/?scope=admin|student&student_ps=... — 폴더/파일 목록 (전체, 클라이언트에서 필터)."""

    @method_decorator(_tenant_required)
    def get(self, request):
        scope = (request.GET.get("scope") or "admin").lower()
        if scope not in ("admin", "student"):
            return JsonResponse({"detail": "Invalid scope"}, status=400)
        student_ps = (request.GET.get("student_ps") or "").strip()
        if scope == "student" and not student_ps:
            return JsonResponse({"detail": "student_ps required for student scope"}, status=400)

        tenant = request.tenant
        qs_folders = InventoryFolder.objects.filter(tenant=tenant, scope=scope)
        if scope == "student":
            qs_folders = qs_folders.filter(student_ps=student_ps)
        qs_files = InventoryFile.objects.filter(tenant=tenant, scope=scope)
        if scope == "student":
            qs_files = qs_files.filter(student_ps=student_ps)

        folders = [
            {"id": str(f.id), "name": f.name, "parentId": str(f.parent_id) if f.parent_id else None}
            for f in qs_folders
        ]
        files = [
            {
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
            for f in qs_files
        ]
        return JsonResponse({"folders": folders, "files": files})


class FolderCreateView(View):
    """POST /storage/inventory/folders/ — 폴더 생성."""

    @method_decorator(_tenant_required)
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

        tenant = request.tenant
        parent = None
        if parent_id:
            parent = InventoryFolder.objects.filter(tenant=tenant, id=parent_id).first()
            if not parent:
                return JsonResponse({"detail": "Parent folder not found"}, status=404)

        folder = InventoryFolder.objects.create(
            tenant=tenant,
            scope=scope,
            student_ps=student_ps,
            parent=parent,
            name=name,
        )
        return JsonResponse({
            "id": str(folder.id),
            "name": folder.name,
            "parentId": str(folder.parent_id) if folder.parent_id else None,
        })


class FileUploadView(View):
    """POST /storage/inventory/upload/ — R2 업로드 후 DB 저장. 플랜 한도 체크."""

    @method_decorator(_tenant_required)
    def post(self, request):
        scope = (request.POST.get("scope") or "admin").lower()
        student_ps = (request.POST.get("student_ps") or "").strip()
        folder_id = request.POST.get("folder_id")
        display_name = (request.POST.get("display_name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        icon = (request.POST.get("icon") or "file-text").strip()
        file_obj = request.FILES.get("file")
        if not file_obj:
            return JsonResponse({"detail": "file required"}, status=400)
        if scope == "student" and not student_ps:
            return JsonResponse({"detail": "student_ps required for student scope"}, status=400)

        tenant = request.tenant
        # Quota
        try:
            program = Program.ensure_for_tenant(tenant=tenant)
            plan = (program.plan or "basic").lower()
        except Exception:
            plan = "basic"
        limit = QUOTA_BYTES.get(plan, QUOTA_BYTES["basic"])
        if plan == "lite":
            return JsonResponse({"detail": "인벤토리 기능을 사용할 수 없는 플랜입니다.", "code": "plan_lite"}, status=403)
        used = InventoryFile.objects.filter(tenant=tenant).aggregate(s=Sum("size_bytes"))["s"] or 0
        if used + file_obj.size > limit:
            return JsonResponse({"detail": "용량 한도를 초과했습니다. 플랜 업그레이드가 필요합니다.", "code": "quota_exceeded"}, status=403)

        folder = None
        folder_path = ""
        if folder_id:
            folder = InventoryFolder.objects.filter(tenant=tenant, id=folder_id).first()
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

        if upload_fileobj_to_r2:
            try:
                upload_fileobj_to_r2(
                    fileobj=file_obj,
                    key=r2_key,
                    content_type=file_obj.content_type or "application/octet-stream",
                )
            except Exception as e:
                return JsonResponse({"detail": f"R2 upload failed: {e}"}, status=502)

        inv_file = InventoryFile.objects.create(
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
        return JsonResponse({
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
        })


class FolderDeleteView(View):
    """DELETE /storage/inventory/folders/:id/ — 비어있을 때만 삭제."""

    @method_decorator(_tenant_required)
    def delete(self, request, folder_id):
        tenant = request.tenant
        scope = (request.GET.get("scope") or "admin").lower()
        student_ps = (request.GET.get("student_ps") or "").strip()

        folder = InventoryFolder.objects.filter(tenant=tenant, id=folder_id).first()
        if not folder:
            return JsonResponse({"detail": "Not found"}, status=404)
        if folder.scope != scope or (scope == "student" and folder.student_ps != student_ps):
            return JsonResponse({"detail": "Forbidden"}, status=403)

        if InventoryFolder.objects.filter(tenant=tenant, parent=folder).exists():
            return JsonResponse({"detail": "비어있지 않은 폴더는 지울 수 없습니다. 먼저 하위 파일·폴더를 비우거나 삭제하세요.", "code": "folder_not_empty"}, status=400)
        if InventoryFile.objects.filter(tenant=tenant, folder=folder).exists():
            return JsonResponse({"detail": "비어있지 않은 폴더는 지울 수 없습니다. 먼저 하위 파일·폴더를 비우거나 삭제하세요.", "code": "folder_not_empty"}, status=400)

        folder.delete()
        return JsonResponse({}, status=204)


class FileDeleteView(View):
    """DELETE /storage/inventory/files/:id/ — DB 삭제 후 R2 객체 삭제."""

    @method_decorator(_tenant_required)
    def delete(self, request, file_id):
        tenant = request.tenant
        scope = (request.GET.get("scope") or "admin").lower()
        student_ps = (request.GET.get("student_ps") or "").strip()

        inv_file = InventoryFile.objects.filter(tenant=tenant, id=file_id).first()
        if not inv_file:
            return JsonResponse({"detail": "Not found"}, status=404)
        if inv_file.scope != scope or (scope == "student" and inv_file.student_ps != student_ps):
            return JsonResponse({"detail": "Forbidden"}, status=403)

        r2_key = inv_file.r2_key
        inv_file.delete()
        # R2 객체 삭제 (선택: 구현 시 s3.delete_object 호출)
        # if delete_r2_object:
        #     delete_r2_object(r2_key)
        return JsonResponse({}, status=204)


class PresignView(View):
    """POST /storage/inventory/presign/ — R2 presigned GET URL."""

    @method_decorator(_tenant_required)
    def post(self, request):
        import json
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)
        r2_key = body.get("r2_key")
        expires_in = int(body.get("expires_in") or 3600)
        if not r2_key:
            return JsonResponse({"detail": "r2_key required"}, status=400)
        if not generate_presigned_get_url:
            return JsonResponse({"url": ""}, status=200)
        url = generate_presigned_get_url(key=r2_key, expires_in=expires_in)
        return JsonResponse({"url": url})
