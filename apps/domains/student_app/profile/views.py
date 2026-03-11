# apps/domains/student_app/profile/views.py
import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from apps.core.models.user import user_display_username, user_internal_username
from apps.domains.student_app.permissions import IsStudent, IsStudentOrParent, get_request_student
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)


def _get_profile_photo_url(student):
    """Return presigned R2 URL for profile photo, or None."""
    r2_key = getattr(student, "profile_photo_r2_key", None) or ""
    if r2_key:
        try:
            from django.conf import settings
            from libs.s3_client.presign import create_presigned_get_url
            return create_presigned_get_url(r2_key, expires_in=3600, bucket=settings.R2_STORAGE_BUCKET)
        except Exception:
            logger.warning("Failed to generate presigned URL for profile photo r2_key=%s", r2_key)
    # Fallback to local file (legacy)
    if student.profile_photo:
        try:
            return student.profile_photo.url
        except Exception:
            pass
    return None


def _profile_response(request, student, *, is_parent_read_only=False, parent_display_name=None):
    url = _get_profile_photo_url(student)
    payload = {
        "id": student.id,
        "name": student.name,
        "profile_photo_url": url,
        "ps_number": getattr(student, "ps_number", "") or "",
        "username": user_display_username(student.user) if student.user_id else "",
        "parent_phone": getattr(student, "parent_phone", "") or "",
        "phone": (student.phone or "").strip(),
        "gender": (student.gender or "").strip() or None,
        "address": (student.address or "").strip() or None,
        "school_type": getattr(student, "school_type", "HIGH") or "HIGH",
        "high_school": (getattr(student, "high_school", None) or "").strip() or None,
        "middle_school": (getattr(student, "middle_school", None) or "").strip() or None,
        "origin_middle_school": (getattr(student, "origin_middle_school", None) or "").strip() or None,
        "grade": getattr(student, "grade", None),
        "high_school_class": (getattr(student, "high_school_class", None) or "").strip() or None,
        "major": (getattr(student, "major", None) or "").strip() or None,
        "memo": (getattr(student, "memo", None) or "").strip() or None,
    }
    if is_parent_read_only:
        payload["isParentReadOnly"] = True
        payload["displayName"] = parent_display_name or "학생 학부모님"
    return Response(payload)


class StudentProfileView(APIView):
    """
    GET/PATCH /api/student-app/me/
    - 학생 또는 학부모 접근 (학부모는 연결된 학생 프로필 읽기 전용)
    - GET: 프로필 정보. 학부모일 때 isParentReadOnly, displayName("{이름} 학생 학부모님") 추가
    - PATCH: 학생만 가능. 학부모는 403
    """
    permission_classes = [IsAuthenticated, IsStudentOrParent]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "학생 프로필이 없습니다."}, status=404)
        is_parent = getattr(request.user, "parent_profile", None) is not None
        if is_parent:
            display_name = f"{student.name} 학생 학부모님" if student.name else "학생 학부모님"
            return _profile_response(request, student, is_parent_read_only=True, parent_display_name=display_name)
        return _profile_response(request, student)

    def patch(self, request):
        if getattr(request.user, "parent_profile", None) is not None:
            return Response({"detail": "학부모는 프로필을 수정할 수 없습니다."}, status=403)
        student = get_request_student(request)
        if not student:
            return Response({"detail": "학생 프로필이 없습니다."}, status=404)

        # 1) 프로필 사진 (multipart) → R2 업로드
        photo = request.FILES.get("profile_photo")
        if photo:
            if not (photo.content_type and photo.content_type.startswith("image/")):
                return Response({"detail": "이미지 파일만 업로드할 수 있습니다."}, status=400)
            # R2에 업로드
            try:
                import uuid
                from apps.core.r2_paths import profile_photo_key
                from libs.s3_client.client import upload_fileobj

                ext = (photo.name or "photo.jpg").rsplit(".", 1)[-1].lower() or "jpg"
                if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
                    ext = "jpg"
                r2_key = profile_photo_key(
                    tenant_id=student.tenant_id,
                    student_id=student.id,
                    unique_id=str(uuid.uuid4())[:8],
                    ext=ext,
                )
                upload_fileobj(photo, r2_key, content_type=photo.content_type)
                student.profile_photo_r2_key = r2_key
                student.save(update_fields=["profile_photo_r2_key"])
                return _profile_response(request, student)
            except Exception as e:
                logger.error("R2 profile photo upload failed: %s", e)
                # Fallback to local storage
                student.profile_photo = photo
                student.save(update_fields=["profile_photo"])
                return _profile_response(request, student)

        # 2) JSON: name, username, 비밀번호 변경
        data = getattr(request, "data", None) or {}
        if not data and request.content_type and "application/json" in request.content_type:
            import json
            try:
                data = json.loads(request.body) if getattr(request, "body", None) else {}
            except Exception:
                pass

        name = data.get("name")
        if name is not None:
            name = str(name).strip()
            if name:
                student.name = name[:50]
                student.save(update_fields=["name"])

        new_username = data.get("username")
        if new_username is not None:
            new_username = str(new_username).strip()
            if new_username:
                internal = user_internal_username(student.tenant, new_username)
                if User.objects.filter(username=internal).exclude(pk=student.user_id).exists():
                    return Response({"detail": "이미 사용 중인 아이디입니다."}, status=400)
                student.user.username = internal
                student.user.save(update_fields=["username"])

        # 학생 본인 수정 가능 필드 (선생앱 학생 스펙과 동일)
        update_fields = []
        if "phone" in data:
            raw = (data.get("phone") or "").strip().replace("-", "").replace(" ", "")
            student.phone = raw[:20] if raw else None
            update_fields.append("phone")
        if "parent_phone" in data:
            raw = (data.get("parent_phone") or "").strip().replace("-", "").replace(" ", "")
            if raw:
                student.parent_phone = raw[:20]
                update_fields.append("parent_phone")
        if "gender" in data:
            g = (data.get("gender") or "").strip().upper()[:1]
            student.gender = g if g in ("M", "F") else None
            update_fields.append("gender")
        if "address" in data:
            student.address = (data.get("address") or "").strip()[:255] or None
            update_fields.append("address")
        # 회원가입 모달과 동일한 학교·추가 정보 필드
        if "school_type" in data:
            st = (data.get("school_type") or "").strip().upper()
            if st in ("HIGH", "MIDDLE"):
                student.school_type = st
                update_fields.append("school_type")
        if "high_school" in data:
            student.high_school = (data.get("high_school") or "").strip()[:100] or None
            update_fields.append("high_school")
        if "middle_school" in data:
            student.middle_school = (data.get("middle_school") or "").strip()[:100] or None
            update_fields.append("middle_school")
        if "origin_middle_school" in data:
            student.origin_middle_school = (data.get("origin_middle_school") or "").strip()[:100] or None
            update_fields.append("origin_middle_school")
        if "grade" in data:
            raw = data.get("grade")
            if raw is not None and raw != "":
                try:
                    g = int(raw)
                    student.grade = g if 1 <= g <= 3 else None
                except (TypeError, ValueError):
                    student.grade = None
            else:
                student.grade = None
            update_fields.append("grade")
        if "high_school_class" in data:
            student.high_school_class = (data.get("high_school_class") or "").strip()[:100] or None
            update_fields.append("high_school_class")
        if "major" in data:
            student.major = (data.get("major") or "").strip()[:50] or None
            update_fields.append("major")
        if "memo" in data:
            student.memo = (data.get("memo") or "").strip() or None
            update_fields.append("memo")
        if update_fields:
            student.save(update_fields=update_fields)

        current_password = data.get("current_password")
        new_password = data.get("new_password")
        if current_password is not None and new_password is not None:
            if not request.user.check_password(current_password):
                return Response({"detail": "현재 비밀번호가 일치하지 않습니다."}, status=400)
            if not str(new_password).strip() or len(str(new_password)) < 6:
                return Response({"detail": "새 비밀번호는 6자 이상이어야 합니다."}, status=400)
            request.user.set_password(new_password)
            request.user.save(update_fields=["password"])

        return _profile_response(request, student)
