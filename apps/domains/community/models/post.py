from django.db import models
from apps.core.models import Tenant


SUPPORT_KIND_CHOICES = [
    ("bug", "버그"),
    ("feedback", "피드백"),
]
SUPPORT_TITLE_PREFIXES = {
    "[BUG]": "bug",
    "[FB]": "feedback",
    "[FEEDBACK]": "feedback",
}


def platform_support_kind_q(kind: str):
    """명시 필드와 staff가 작성한 기존 board prefix만 지원 티켓으로 분류."""
    if kind not in dict(SUPPORT_KIND_CHOICES):
        return models.Q(pk__in=[])
    legacy_prefixes = [
        prefix
        for prefix, prefix_kind in SUPPORT_TITLE_PREFIXES.items()
        if prefix_kind == kind
    ]
    legacy_title_q = models.Q(pk__in=[])
    for prefix in legacy_prefixes:
        legacy_title_q |= models.Q(title__startswith=prefix)
    return models.Q(support_kind=kind) | (
        models.Q(
            support_kind__isnull=True,
            post_type="board",
            author_role="staff",
        )
        & legacy_title_q
    )


def platform_support_q():
    """신규 명시 필드 + 제한된 기존 제목 prefix compatibility filter."""
    return platform_support_kind_q("bug") | platform_support_kind_q("feedback")


def support_kind_for_post(post) -> str | None:
    explicit = getattr(post, "support_kind", None)
    if explicit in dict(SUPPORT_KIND_CHOICES):
        return explicit
    if (
        getattr(post, "post_type", None) != "board"
        or getattr(post, "author_role", None) != "staff"
    ):
        return None
    title = str(getattr(post, "title", "") or "")
    for prefix, kind in SUPPORT_TITLE_PREFIXES.items():
        if title.startswith(prefix):
            return kind
    return None


def support_subject(title: str) -> str:
    value = str(title or "")
    for prefix in SUPPORT_TITLE_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


POST_TYPE_CHOICES = [
    ("notice", "공지사항"),
    ("board", "게시판"),
    ("materials", "자료실"),  # 일방향 다운로드 정책 — 댓글 비활성 (DOWNLOAD_ONLY_POST_TYPES 참조)
    ("qna", "QnA"),
    ("counsel", "상담 신청"),
]

# 학생 가시성 정책: 공지·게시판·자료실은 모두에게 공개, QnA·상담은 작성자+staff만.
STUDENT_PUBLIC_POST_TYPES = frozenset({"notice", "board", "materials"})

# 댓글 비활성 정책 — 일방향 컨텐츠로 운영. staff/student 모두 reply 등록 차단.
# 자료실: 강의 자료 다운로드 전용. 의견 교환은 게시판/QnA로 분리.
DOWNLOAD_ONLY_POST_TYPES = frozenset({"materials"})

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
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True, default="")
    category_label = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="학생이 선택한 카테고리 (수강 중인 강의명 등)",
    )
    support_kind = models.CharField(
        max_length=20,
        choices=SUPPORT_KIND_CHOICES,
        null=True,
        blank=True,
        db_index=True,
        help_text="개발자 비공개 지원 티켓 분류. null이면 일반 커뮤니티 글.",
    )
    support_request_key = models.CharField(
        max_length=80,
        null=True,
        blank=True,
        help_text="비공개 지원 티켓 생성 재시도 식별자.",
    )
    created_by = models.ForeignKey(
        "students.Student",
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
    meta = models.JSONField(default=dict, blank=True, help_text="AI 매치업 결과 등 확장 데이터")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "post_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "support_request_key"],
                condition=models.Q(support_request_key__isnull=False),
                name="comm_post_support_req_uq",
            ),
        ]
        verbose_name = "Post"
        verbose_name_plural = "Posts"

    def __str__(self):
        return self.title
