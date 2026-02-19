# apps/domains/student_app/profile/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from apps.domains.student_app.permissions import IsStudent, get_request_student


class StudentProfileView(APIView):
    """
    GET/PATCH /api/student-app/me/
    - 학생만 접근 가능 (본인 프로필)
    - GET: 프로필 정보 (id, name, profile_photo_url)
    - PATCH: profile_photo 업로드 (multipart/form-data)
    """
    permission_classes = [IsAuthenticated, IsStudent]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "학생 프로필이 없습니다."}, status=404)
        url = None
        if student.profile_photo:
            url = request.build_absolute_uri(student.profile_photo.url)
        return Response({
            "id": student.id,
            "name": student.name,
            "profile_photo_url": url,
            "ps_number": getattr(student, "ps_number", "") or "",
        })

    def patch(self, request):
        student = get_request_student(request)
        if not student:
            return Response({"detail": "학생 프로필이 없습니다."}, status=404)
        photo = request.FILES.get("profile_photo")
        if not photo:
            return Response({"detail": "profile_photo 파일이 필요합니다."}, status=400)
        # 이미지 타입 제한 (선택)
        if not photo.content_type or not photo.content_type.startswith("image/"):
            return Response({"detail": "이미지 파일만 업로드할 수 있습니다."}, status=400)
        student.profile_photo = photo
        student.save(update_fields=["profile_photo"])
        url = request.build_absolute_uri(student.profile_photo.url) if student.profile_photo else None
        return Response({
            "id": student.id,
            "name": student.name,
            "profile_photo_url": url,
            "ps_number": getattr(student, "ps_number", "") or "",
        })
