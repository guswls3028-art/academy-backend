from django.contrib import admin
from .models import Session, SessionParticipant, Test, Submission


def _tenant_filtered_qs(request, qs):
    """superuser는 전체, 그 외는 tenant 멤버십 기준 필터."""
    if request.user.is_superuser:
        return qs
    tenant_ids = request.user.tenant_memberships.filter(
        is_active=True, role__in=["owner", "admin", "teacher", "staff"]
    ).values_list("tenant_id", flat=True)
    return qs.filter(tenant_id__in=tenant_ids)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "date",
        "start_time",
        "location",
        "max_participants",
        "created_by",
        "created_at",
    )
    list_filter = ("tenant", "date", "location")
    search_fields = ("location",)
    ordering = ("-date", "-start_time")

    def get_queryset(self, request):
        return _tenant_filtered_qs(request, super().get_queryset(request))


@admin.register(SessionParticipant)
class SessionParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "session",
        "student",
        "status",
        "source",
        "participant_role",
        "enrollment_id",
        "clinic_reason",
        "status_changed_at",
        "status_changed_by",
        "created_at",
    )
    list_filter = (
        "status",
        "source",
        "participant_role",
        "clinic_reason",
        "session__date",
    )
    search_fields = ("student__name", "session__location")
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return _tenant_filtered_qs(request, super().get_queryset(request))


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "title", "session", "round", "date")
    list_filter = ("tenant", "session", "date")
    search_fields = ("title",)
    ordering = ("-date",)

    def get_queryset(self, request):
        return _tenant_filtered_qs(request, super().get_queryset(request))


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "test", "student", "status", "score", "graded_at", "created_at")
    list_filter = ("tenant", "status", "test__session__date")
    search_fields = ("student__name", "test__title")
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return _tenant_filtered_qs(request, super().get_queryset(request))
