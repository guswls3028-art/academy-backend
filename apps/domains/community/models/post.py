from django.db import models
from apps.core.models import Tenant
from apps.domains.students.models import Student
from .block_type import BlockType


class PostEntity(models.Model):
    """콘텐츠 단일 객체. 노출 위치는 PostMapping으로 관리. tenant 필수."""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="post_entities",
        null=False,
        db_index=True,
    )
    block_type = models.ForeignKey(
        BlockType,
        on_delete=models.PROTECT,
        related_name="posts",
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
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
        help_text="작성자 표시명 (관리자 글: 관리자 이름 저장, 학생 글: created_by에서 파생)",
    )
    is_urgent = models.BooleanField(default=False, help_text="긴급 공지 여부")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "block_type"]),
        ]
        verbose_name = "Post"
        verbose_name_plural = "Posts"

    def __str__(self):
        return self.title
