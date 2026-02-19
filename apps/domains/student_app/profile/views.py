# apps/domains/student_app/profile/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from apps.core.models.user import user_display_username, user_internal_username
from apps.domains.student_app.permissions import IsStudent, get_request_student
from django.contrib.auth import get_user_model

User = get_user_model()


def _profile_response(request, student):
    url = None
    if student.profile_photo:
        url = request.build_absolute_uri(student.profile_photo.url)
    return Response({
        "id": student.id,
        "name": student.name,
        "profile_photo_url": url,
        "ps_number": getattr(student, "ps_number", "") or "",
        "username": user_display_username(student.user) if student.user_id else "",
        "parent_phone": getattr(student, "parent_phone", "") or "",
    })


class StudentProfileView(APIView):
    """
    GET/PATCH /api/student-app/me/
    - 학생만 접근 가능 (본인 프로필)
    - GET: 프로필 정보 (id, name, profile_photo_url, ps_number, username, parent_phone)
    - PATCH: profile_photo(multipart) 또는 JSON(name, username, current_password, new_password)
    """
    permission_classes = [IsAuthenticated, IsStudent]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "학생 프로필이 없습니다."}, status=404)
        return _profile_response(request, student)

    def patch(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "학생 프로필이 없습니다."}, status=404)

        # 1) 프로필 사진 (multipart)
        photo = request.FILES.get("profile_photo")
        if photo:
            if not (photo.content_type and photo.content_type.startswith("image/")):
                return Response({"detail": "이미지 파일만 업로드할 수 있습니다."}, status=400)
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
