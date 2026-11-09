"""Validate and save the images and reply as a single visible operation."""

import hashlib
import logging
from dataclasses import dataclass

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from PIL import Image
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.domains.community.models import PostEntity, PostReplyAttachment
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
from apps.support.community.storage_cleanup_dependencies import (
    lock_storage_object_keys,
    schedule_detached_storage_cleanup,
)

from apps.domains.community.services.attachment_urls import reply_image_namespace

from ._common import is_attachment_allowed, sanitize_filename

logger = logging.getLogger(__name__)
IMAGE_TYPES = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "GIF": ("image/gif", "gif"),
    "WEBP": ("image/webp", "webp"),
}
MAX_IMAGES = 10
MAX_IMAGE_SIZE = 5 * 1024 * 1024


class ReplyImageSaveError(APIException):
    status_code = 503
    default_detail = "답변 사진을 저장하지 못했습니다. 다시 시도해 주세요."


class ReplyImageConflict(APIException):
    status_code = 409
    default_detail = "같은 등록 요청의 내용이 달라졌습니다. 이미 등록된 답변을 확인해 주세요."


@dataclass(frozen=True)
class ReplyImageUpload:
    file: UploadedFile
    name: str
    content_type: str
    key: str


def plan_reply_images(*, files, prefix):
    if not 1 <= len(files) <= MAX_IMAGES:
        raise ValidationError({"detail": "답변 사진은 1장부터 최대 10장까지 첨부할 수 있습니다."})

    planned = []
    for index, file in enumerate(files):
        if not 0 < file.size <= MAX_IMAGE_SIZE:
            raise ValidationError({"detail": f"'{file.name}': 5MB 이하의 사진을 첨부해 주세요."})
        allowed, reason = is_attachment_allowed(file.name, file.content_type)
        if not allowed:
            raise ValidationError({"detail": f"'{file.name}': {reason}"})

        try:
            with Image.open(file, formats=list(IMAGE_TYPES)) as image:
                content_type, suffix = IMAGE_TYPES[image.format]
                image.verify()
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValidationError({"detail": f"'{file.name}': JPG, PNG, GIF 또는 WebP 사진을 첨부해 주세요."}) from exc
        finally:
            file.seek(0)

        name = sanitize_filename(file.name)
        digest = hashlib.sha256()
        for chunk in file.chunks():
            digest.update(chunk)
        file.seek(0)
        name_hash = hashlib.sha256(name.encode()).hexdigest()[:8]
        key = f"{prefix}{index}_{digest.hexdigest()[:16]}_{name_hash}_photo.{suffix}"
        planned.append(ReplyImageUpload(file, name, content_type, key))
    return planned


def reply_image_prefix(post, user_id, request_key):
    batch = hashlib.sha256(f"{user_id}:{request_key}".encode()).hexdigest()[:32]
    return f"{reply_image_namespace(post.tenant_id, post.id)}{batch}/"


def create_reply_with_images(*, post, content, prefix, uploads, save_reply):
    keys = {upload.key for upload in uploads}
    attempted = []
    try:
        with transaction.atomic():
            if not PostEntity.objects.select_for_update().filter(
                pk=post.pk, tenant_id=post.tenant_id, post_type="qna",
            ).exists():
                raise NotFound()

            existing = list(PostReplyAttachment.objects.filter(
                reply__post=post, reply__tenant_id=post.tenant_id, r2_key__startswith=prefix,
            ).select_related("reply"))
            if existing:
                reply = existing[0].reply
                if (
                    {image.r2_key for image in existing} != keys
                    or any(image.reply_id != reply.id for image in existing)
                    or reply.content != content
                ):
                    raise ReplyImageConflict()
                return reply, False

            lock_storage_object_keys(tenant_id=post.tenant_id, object_keys=keys)
            reply = save_reply()
            for upload in uploads:
                attempted.append(upload.key)
                upload_fileobj_to_r2_storage(
                    fileobj=upload.file, key=upload.key, content_type=upload.content_type,
                )
                PostReplyAttachment.objects.create(
                    reply=reply, r2_key=upload.key, original_name=upload.name,
                    size_bytes=upload.file.size, content_type=upload.content_type,
                )
        return reply, True
    except (NotFound, ReplyImageConflict):
        raise
    except Exception as exc:
        logger.exception("Q&A reply image save failed: post_id=%s", post.id)
        if attempted:
            # Recheck ownership after rollback, including an upload whose response was lost.
            with transaction.atomic():
                schedule_detached_storage_cleanup(tenant_id=post.tenant_id, object_keys=attempted)
        raise ReplyImageSaveError() from exc
