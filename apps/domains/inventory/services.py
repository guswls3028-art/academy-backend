# PATH: apps/domains/inventory/services.py
# 이동 로직: R2 Copy → DB 업데이트 → R2 Delete (실패 시 원본 삭제 안 함)

from __future__ import annotations

import json
from django.db import transaction

from .models import InventoryFolder, InventoryFile
from .r2_path import build_r2_key, folder_path_string, safe_filename
from apps.core.models import Tenant

try:
    from apps.infrastructure.storage.r2 import (
        copy_object_r2_storage,
        delete_object_r2_storage,
    )
except ImportError:
    copy_object_r2_storage = None
    delete_object_r2_storage = None


def _folder_path_parts(folder: InventoryFolder | None, tenant: Tenant, scope: str, student_ps: str) -> list[str]:
    """폴더부터 루트까지 이름 리스트 (루트가 마지막)."""
    parts = []
    f = folder
    while f:
        parts.append(f.name)
        f = f.parent
    return list(reversed(parts))


def _get_folder_path_str(folder: InventoryFolder | None, tenant: Tenant, scope: str, student_ps: str) -> str:
    return folder_path_string(_folder_path_parts(folder, tenant, scope, student_ps))


def _filename_from_r2_key(r2_key: str) -> str:
    """R2 key에서 마지막 파일명만 추출."""
    return r2_key.split("/")[-1] if r2_key else ""


def _check_duplicate_file(target_folder_id: int | None, tenant: Tenant, scope: str, student_ps: str, display_name: str):
    qs = InventoryFile.objects.filter(tenant=tenant, scope=scope, folder_id=target_folder_id)
    if scope == "student":
        qs = qs.filter(student_ps=student_ps)
    return qs.filter(display_name=display_name).first()


def move_file(
    *,
    tenant: Tenant,
    scope: str,
    student_ps: str,
    source_file_id: int,
    target_folder_id: int | None,
    on_duplicate: str = "rename",
) -> dict:
    """
    파일 이동: R2 Copy → DB 업데이트 → R2 Delete.
    실패 시 원본 R2 객체는 삭제하지 않음.
    """
    if not copy_object_r2_storage or not delete_object_r2_storage:
        return {"ok": False, "detail": "R2 storage not configured"}

    source = InventoryFile.objects.filter(tenant=tenant, id=source_file_id).first()
    if not source or source.scope != scope or (scope == "student" and source.student_ps != student_ps):
        return {"ok": False, "detail": "Source file not found", "status": 404}

    target_folder = None
    if target_folder_id:
        target_folder = InventoryFolder.objects.filter(tenant=tenant, id=target_folder_id).first()
        if not target_folder or target_folder.scope != scope or (scope == "student" and target_folder.student_ps != student_ps):
            return {"ok": False, "detail": "Target folder not found", "status": 404}

    if source.folder_id == target_folder_id:
        return {"ok": True, "detail": "Already in target"}

    target_path = _get_folder_path_str(target_folder, tenant, scope, student_ps)
    current_filename = _filename_from_r2_key(source.r2_key)
    display_name = source.display_name

    existing = _check_duplicate_file(target_folder_id, tenant, scope, student_ps, display_name)
    if existing and existing.id != source_file_id:
        if on_duplicate == "overwrite":
            existing_key = existing.r2_key
            existing.delete()
            try:
                delete_object_r2_storage(key=existing_key)
            except Exception:
                pass
        elif on_duplicate == "rename":
            base, ext = "", ""
            if "." in display_name:
                idx = display_name.rfind(".")
                base, ext = display_name[:idx], display_name[idx:]
            else:
                base = display_name
            display_name = f"{base}_복사본{ext}" if ext else f"{base}_복사본"
            current_filename = safe_filename(display_name)
        else:
            return {"ok": False, "status": 409, "code": "duplicate", "existing_name": display_name, "detail": "File with same name exists"}

    new_key = build_r2_key(
        tenant_id=tenant.id,
        scope=scope,
        student_ps=student_ps,
        folder_path=target_path,
        file_name=current_filename,
    )

    old_key = source.r2_key
    try:
        copy_object_r2_storage(source_key=old_key, dest_key=new_key)
    except Exception as e:
        return {"ok": False, "detail": f"R2 copy failed: {e}", "status": 502}

    try:
        with transaction.atomic():
            source.folder_id = target_folder_id
            source.r2_key = new_key
            source.display_name = display_name
            source.save(update_fields=["folder_id", "r2_key", "display_name", "updated_at"])
    except Exception as e:
        return {"ok": False, "detail": f"DB update failed: {e}", "status": 500}

    try:
        delete_object_r2_storage(key=old_key)
    except Exception as e:
        return {"ok": False, "detail": f"R2 delete failed (data updated): {e}", "status": 500}

    return {"ok": True}


def _collect_folder_tree(folder: InventoryFolder, tenant: Tenant, scope: str, student_ps: str):
    """폴더와 그 하위 모든 폴더·파일 수집."""
    folders = [folder]
    files = list(InventoryFile.objects.filter(tenant=tenant, scope=scope, folder=folder))
    if scope == "student":
        files = [f for f in files if f.student_ps == student_ps]
    for child in InventoryFolder.objects.filter(tenant=tenant, parent=folder):
        if scope == "student" and child.student_ps != student_ps:
            continue
        sub_f, sub_files = _collect_folder_tree(child, tenant, scope, student_ps)
        folders.extend(sub_f)
        files.extend(sub_files)
    return folders, files


def _file_folder_path(inv_file: InventoryFile, tenant: Tenant, scope: str, student_ps: str) -> str:
    """파일이 속한 폴더의 경로 문자열 (prefix 제외)."""
    return _get_folder_path_str(inv_file.folder, tenant, scope, student_ps)


def move_folder(
    *,
    tenant: Tenant,
    scope: str,
    student_ps: str,
    source_folder_id: int,
    target_folder_id: int | None,
    on_duplicate: str = "rename",
) -> dict:
    """
    폴더 이동: 하위 모든 파일에 대해 R2 Copy → DB 업데이트 → R2 Delete.
    폴더 자체의 parent_id 업데이트 포함. 실패 시 원본 삭제 안 함.
    """
    if not copy_object_r2_storage or not delete_object_r2_storage:
        return {"ok": False, "detail": "R2 storage not configured"}

    source_folder = InventoryFolder.objects.filter(tenant=tenant, id=source_folder_id).first()
    if not source_folder or source_folder.scope != scope or (scope == "student" and source_folder.student_ps != student_ps):
        return {"ok": False, "detail": "Source folder not found", "status": 404}

    target_folder = None
    if target_folder_id:
        target_folder = InventoryFolder.objects.filter(tenant=tenant, id=target_folder_id).first()
        if not target_folder or target_folder.scope != scope or (scope == "student" and target_folder.student_ps != student_ps):
            return {"ok": False, "detail": "Target folder not found", "status": 404}
        f = target_folder
        while f:
            if f.id == source_folder_id:
                return {"ok": False, "detail": "Cannot move folder into itself or descendant", "status": 400}
            f = f.parent

    if source_folder.parent_id == target_folder_id:
        return {"ok": True, "detail": "Already in target"}

    folders, files = _collect_folder_tree(source_folder, tenant, scope, student_ps)
    source_folder_path = _get_folder_path_str(source_folder, tenant, scope, student_ps)
    target_path_str = _get_folder_path_str(target_folder, tenant, scope, student_ps)

    prefix = f"tenants/{tenant.id}/students/{student_ps}/inventory" if scope == "student" else f"tenants/{tenant.id}/admin/inventory"

    copy_plans = []
    for inv_file in files:
        old_key = inv_file.r2_key
        file_name = _filename_from_r2_key(old_key)
        current_folder_path = _file_folder_path(inv_file, tenant, scope, student_ps)
        if current_folder_path.startswith(source_folder_path):
            rel = current_folder_path[len(source_folder_path):].lstrip("/")
        else:
            rel = ""
        if rel:
            new_folder_path = f"{target_path_str}/{source_folder.name}/{rel}" if target_path_str else f"{source_folder.name}/{rel}"
        else:
            new_folder_path = f"{target_path_str}/{source_folder.name}" if target_path_str else source_folder.name
        new_key = build_r2_key(
            tenant_id=tenant.id,
            scope=scope,
            student_ps=student_ps,
            folder_path=new_folder_path,
            file_name=file_name,
        )
        copy_plans.append((inv_file, old_key, new_key))

    for inv_file, old_key, new_key in copy_plans:
        try:
            copy_object_r2_storage(source_key=old_key, dest_key=new_key)
        except Exception as e:
            return {"ok": False, "detail": f"R2 copy failed: {e}", "status": 502}

    try:
        with transaction.atomic():
            for inv_file, old_key, new_key in copy_plans:
                inv_file.r2_key = new_key
                inv_file.save(update_fields=["r2_key", "updated_at"])
            source_folder.parent = target_folder
            source_folder.save(update_fields=["parent_id", "updated_at"])
    except Exception as e:
        return {"ok": False, "detail": f"DB update failed: {e}", "status": 500}

    for inv_file, old_key, new_key in copy_plans:
        try:
            delete_object_r2_storage(key=old_key)
        except Exception:
            pass

    return {"ok": True}
