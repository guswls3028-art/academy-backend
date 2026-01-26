# FILE: views.py
from django.db import transaction
from django.contrib.auth import get_user_model

from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.core.permissions import IsAdminOrStaff, IsStudent

from .models import Student, Tag, StudentTag
from .filters import StudentFilter
from .serializers import (
    StudentListSerializer,
    StudentDetailSerializer,
    TagSerializer,
    AddTagSerializer,
)


# ======================================================
# Tag
# ======================================================

class TagViewSet(ModelViewSet):
    """
    학생 태그 관리
    - 관리자 / 스태프 전용
    """
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    permission_classes = [IsAdminOrStaff]


# ======================================================
# Student
# ======================================================

class StudentViewSet(ModelViewSet):
    """
    학생 관리 ViewSet

    ✔ 학생 생성 시 User 계정 자동 생성
    ✔ phone = username
    ✔ 초기 비밀번호는 교사가 설정
    ✔ 학생 CRUD는 관리자만 가능
    """
    queryset = Student.objects.all()
    permission_classes = [IsAdminOrStaff]

    # ------------------------------
    # Serializer 선택
    # ------------------------------
    def get_serializer_class(self):
        if self.action == "create":
            # 생성 전용 (initial_password 포함)
            from .serializers import StudentCreateSerializer
            return StudentCreateSerializer

        if self.action == "list":
            return StudentListSerializer

        return StudentDetailSerializer

    # ------------------------------
    # Student + User 생성
    # ------------------------------
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        학생 생성 시 처리 흐름

        1. 입력값 검증 (StudentCreateSerializer)
        2. User 생성 (username = phone)
        3. Student 생성 + user 연결
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        User = get_user_model()

        phone = serializer.validated_data["phone"]
        password = serializer.validated_data.pop("initial_password")

        # 1️⃣ User 생성
        user = User.objects.create(
            username=phone,
            phone=phone,
            name=serializer.validated_data.get("name", ""),
        )
        user.set_password(password)
        user.save()

        # 2️⃣ Student 생성 + User 연결
        student = Student.objects.create(
            user=user,
            **serializer.validated_data,
        )

        output = StudentDetailSerializer(
            student,
            context={"request": request},
        )
        return Response(output.data, status=201)

    # ------------------------------
    # ✅ DELETE: Student 삭제 시 User도 같이 삭제 (옵션 A)
    # ------------------------------
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        """
        학생 삭제 시 처리 흐름

        ✔ Student 삭제
        ✔ 연결된 User도 같이 삭제 (전화번호 재사용 가능)
        """
        student = self.get_object()
        user = student.user

        # Student 먼저 삭제 (StudentTag 등 through는 CASCADE로 정리됨)
        self.perform_destroy(student)

        # User 같이 삭제
        if user:
            user.delete()

        return Response(status=204)

    # ------------------------------
    # Filtering / Searching / Ordering
    # ------------------------------
    filter_backends = [
        DjangoFilterBackend,
        SearchFilter,
        OrderingFilter,
    ]
    filterset_class = StudentFilter
    search_fields = ["name", "high_school", "major"]
    ordering_fields = ["id", "created_at", "updated_at"]
    ordering = ["-id"]

    # ------------------------------
    # Tag 관리
    # ------------------------------
    @action(detail=True, methods=["post"])
    def add_tag(self, request, pk=None):
        student = self.get_object()
        serializer = AddTagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tag = Tag.objects.get(id=serializer.validated_data["tag_id"])
        StudentTag.objects.get_or_create(student=student, tag=tag)

        return Response({"status": "ok"}, status=201)

    @action(detail=True, methods=["post"])
    def remove_tag(self, request, pk=None):
        student = self.get_object()
        serializer = AddTagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        StudentTag.objects.filter(
            student=student,
            tag_id=serializer.validated_data["tag_id"],
        ).delete()

        return Response({"status": "ok"}, status=200)

    # --------------------------------------------------
    # Anchor API: /students/me/
    # --------------------------------------------------
    @action(
        detail=False,
        methods=["get"],
        url_path="me",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def me(self, request):
        """
        학생 본인 정보 조회 (Anchor API)

        🔒 보안 포인트
        - request.user 기준 강제
        - 다른 학생 ID 접근 불가
        - staff가 호출하면 403
        """
        student = (
            Student.objects
            .select_related("user")
            .prefetch_related("tags", "enrollments")
            .get(user=request.user)
        )

        serializer = StudentDetailSerializer(
            student,
            context={"request": request},
        )
        return Response(serializer.data)