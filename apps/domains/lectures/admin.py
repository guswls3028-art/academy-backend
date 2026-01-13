from django.contrib import admin
from .models import Lecture, Session


# --------------------------------------------------
# Lecture
# --------------------------------------------------

@admin.register(Lecture)
class LectureAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "name",
        "subject",
        "start_date",
        "end_date",
        "is_active",
    )
    list_display_links = ("id", "title")
    list_filter = ("is_active", "subject")
    search_fields = ("title", "name", "subject")
    ordering = ("-id",)


# --------------------------------------------------
# Session
# --------------------------------------------------

@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    """
    Session Admin (차시 관리)

    ⚠️ 중요:
    - Session.exam FK는 제거됨
    - 시험은 Exam.sessions (ManyToMany)로만 연결됨
    - admin에서는 '시험 개수 / 요약'만 노출
    """

    list_display = (
        "id",
        "lecture",
        "order",
        "title",
        "date",
        "exam_count",     # ✅ 대체 컬럼
    )
    list_display_links = ("id", "title")
    list_filter = ("lecture",)
    search_fields = ("title",)
    ordering = ("lecture", "order")

    # --------------------------------------------
    # ✅ 연결된 시험 개수 표시
    # --------------------------------------------
    def exam_count(self, obj: Session) -> int:
        """
        이 차시에 연결된 시험 개수
        (Exam.sessions M2M 기준)
        """
        return obj.exams.count()

    exam_count.short_description = "시험 수"
