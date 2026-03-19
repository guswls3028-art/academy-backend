from django.db import models
from apps.core.models import Tenant
from apps.domains.students.models import Student
from .block_type import BlockType


POST_TYPE_CHOICES = [
    ("notice", "공지사항"),
    ("board", "게시판"),
    ("materials", "자료실"),
    ("qna", "QnA"),
    ("counsel", "상담 신청"),
]

# Student-visible public post types (policy: students see these + their own posts)
STUDENT_PUBLIC_POST_TYPES = frozenset({"notice", "board", "materials"})

VALID_POST_TYPES = {choice[0] for choice in POST_TYPE_CHOICES}


class PostEntity(models.Model):
    """콘텐츠 단일 객체. 노출 위치는 PostMapping으로 관리. tenant 필수."""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="post_entities",
        null=False,
        db_index=True,
    )
    post_type = models.CharField(
        max_length=20,
        choices=POST_TYPE_CHOICES,
        default="board",
        db_index=True,
        help_text="게시글 유형 (notice, board, materials, qna, counsel)",
    )
    block_type = models.ForeignKey(
        BlockType,
        on_delete=models.SET_NULL,
        related_name="posts",
        null=True,
        blank=True,
        help_text="레거시 블록 타입 FK (post_type으로 대체됨)",
    )
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True, default="")
    category_label = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="학생이 선택한 카테고리 (수강 중인 강의명 등)",
    )
    created_by = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="post_entities",
    )
    author_display_name = models.CharField(
        max_length=100, null=True, blank=True,
        help_text="작성자 표시명 (관리자: staff 이름, 학생: created_by에서 파생)",
    )
    author_role = models.CharField(
        max_length=20, default="staff", blank=True,
        help_text="작성자 역할 (staff/student)",
    )
    is_urgent = models.BooleanField(default=False, help_text="긴급 공지 여부")
    is_pinned = models.BooleanField(default=False, help_text="상단 고정 여부")
    status = models.CharField(
        max_length=20,
        default="published",
        choices=[
            ("draft", "임시저장"),
            ("published", "게시됨"),
            ("archived", "보관됨"),
        ],
        db_index=True,
        help_text="게시 상태 (draft=임시저장, published=게시됨, archived=보관됨)",
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="예약 게시 시각. null이면 즉시 게시.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "post_type"]),
        ]
        verbose_name = "Post"
        verbose_name_plural = "Posts"

    def __str__(self):
        return self.title
