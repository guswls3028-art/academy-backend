from __future__ import annotations

import logging
import uuid

from django.db import transaction

from apps.api.common.image_validator import is_real_image
from apps.core.r2_paths import profile_photo_key
from apps.domains.students.models import Student


logger = logging.getLogger(__name__)


class StudentProfilePhotoValidationError(ValueError):
    pass


class StudentProfilePhotoStorageError(RuntimeError):
    pass


def _delete_profile_photo_best_effort(key: str) -> None:
    if not key:
        return
    try:
        from apps.infrastructure.storage.r2 import delete_object_r2_storage

        delete_object_r2_storage(key=key, timeout_seconds=5)
    except Exception:
        logger.warning("Failed to delete profile photo key=%s", key)


def replace_student_profile_photo(*, student: Student, photo) -> Student:
    """Validate and replace one student's tenant-scoped R2 profile photo."""

    if not (photo.content_type and photo.content_type.startswith("image/")):
        raise StudentProfilePhotoValidationError("이미지 파일만 업로드할 수 있습니다.")
    if photo.size and photo.size > 10 * 1024 * 1024:
        raise StudentProfilePhotoValidationError("프로필 사진은 10MB 이하만 업로드할 수 있습니다.")
    if not is_real_image(photo):
        raise StudentProfilePhotoValidationError(
            "이미지 파일이 손상되었거나 이미지 형식이 아닙니다."
        )

    ext = (photo.name or "photo.jpg").rsplit(".", 1)[-1].lower() or "jpg"
    if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
        ext = "jpg"
    new_key = profile_photo_key(
        tenant_id=student.tenant_id,
        student_id=student.id,
        unique_id=str(uuid.uuid4())[:8],
        ext=ext,
    )

    try:
        from academy.adapters.storage.r2_objects import upload_fileobj

        upload_fileobj(photo, new_key, content_type=photo.content_type)
    except Exception as exc:
        _delete_profile_photo_best_effort(new_key)
        raise StudentProfilePhotoStorageError(
            "프로필 사진 저장에 실패했습니다. 잠시 후 다시 시도해 주세요."
        ) from exc

    try:
        with transaction.atomic():
            locked_student = Student.objects.select_for_update().get(
                pk=student.pk,
                tenant_id=student.tenant_id,
            )
            previous_key = locked_student.profile_photo_r2_key or ""
            locked_student.profile_photo_r2_key = new_key
            locked_student.save(update_fields=["profile_photo_r2_key"])
            if previous_key and previous_key != new_key:
                transaction.on_commit(
                    lambda key=previous_key: _delete_profile_photo_best_effort(key)
                )
    except Exception as exc:
        _delete_profile_photo_best_effort(new_key)
        raise StudentProfilePhotoStorageError(
            "프로필 사진 저장에 실패했습니다. 잠시 후 다시 시도해 주세요."
        ) from exc

    student.profile_photo_r2_key = new_key
    return locked_student
