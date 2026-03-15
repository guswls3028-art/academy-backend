# Full-Stack Audit — Phase 1: Backend Findings

**Status:** Code fixes applied. Django check passes.

---

## Implemented fixes (this session)

- **submissions:** SubmissionViewSet get_queryset filter(tenant=...); TenantResolvedAndMember; perform_create/ExamOMR* pass tenant; SubmissionCreateSerializer create() accepts tenant/user; ExamSubmissionsListView filter by tenant; manual_edit SubmissionAnswer defaults tenant.
- **progress:** All 5 ViewSets get_queryset by lecture__tenant or session__lecture__tenant; TenantResolvedAndMember.
- **schedule:** DdayViewSet get_queryset filter(lecture__tenant=...); TenantResolvedAndMember.
- **video:** VideoViewSet get_queryset filter(session__lecture__tenant=...); delete_folder scoped by session__lecture__tenant.
- **exams:** ExamViewSet get_queryset filter(sessions__lecture__tenant=...); TenantResolvedAndMember; REGULAR create verifies session.lecture.tenant. AnswerKeyViewSet/QuestionViewSet/SheetViewSet get_queryset tenant-scoped via exam__sessions__lecture__tenant (or sheet__exam__...).
- **homework_results:** HomeworkViewSet get_queryset filter(session__lecture__tenant=...); TenantResolvedAndMember. HomeworkScoreViewSet get_queryset filter(session__lecture__tenant=...).
- **results:** SessionScoresView get_object_or_404(Session, id=..., lecture__tenant=tenant).

**Note:** Exam has no tenant FK. Template exams (no sessions) are not listed by the current Exam get_queryset; only exams with at least one session in the tenant appear. Consider adding tenant_id to Exam for full template scoping.

---

## Summary: Tenant scoping gaps (confirmed from code)

| Subsystem | Finding | Severity | Fix |
|-----------|---------|----------|-----|
| **submissions** | SubmissionViewSet queryset = Submission.objects.all(); Submission has tenant FK | **CRITICAL** | get_queryset: filter(tenant=request.tenant); require TenantResolvedAndStaff |
| **progress** | All 5 ViewSets use .all() with no tenant filter; models scoped via lecture/session | **CRITICAL** | get_queryset: filter by lecture__tenant or session__lecture__tenant |
| **schedule** | DdayViewSet queryset = Dday.objects.all(); filterset_fields=["lecture"] only | **HIGH** | get_queryset: filter(lecture__tenant=request.tenant) |
| **video** | VideoViewSet uses get_video_queryset_with_relations() which is Video.objects.all() | **CRITICAL** | get_queryset: filter(session__lecture__tenant=request.tenant) |
| **exams** | ExamViewSet, AnswerKeyViewSet, QuestionViewSet, SheetViewSet — no tenant in queryset | HIGH | Exam is via session→lecture; filter by session__lecture__tenant where applicable |
| **homework_results** | HomeworkViewSet, HomeworkScoreViewSet — no tenant filter | HIGH | Filter by session__lecture__tenant or equivalent |
| **results** | SessionScoresView get_object_or_404(Session, id=...) without tenant | HIGH | Resolve session with tenant check |

---

## Confirmed code references

- **Submission model:** `apps/domains/submissions/models/submission.py` — `tenant = models.ForeignKey("core.Tenant", ...)`.
- **SubmissionViewSet:** `apps/domains/submissions/views/submission_view.py:21` — `queryset = Submission.objects.all().order_by("-id")`.
- **Progress ViewSets:** `apps/domains/progress/views.py` — all use `ModelViewSet` with class `queryset = ... .all()`, no get_queryset.
- **Dday:** `apps/domains/schedule/models.py` — Dday has lecture FK; `apps/domains/schedule/views.py` — queryset = Dday.objects.all().
- **Video:** `academy/adapters/db/django/repositories_video.py:23-28` — `get_video_queryset_with_relations()` returns `Video.objects.all().select_related(...)`; Video has session→lecture→tenant (no direct tenant FK).

---

## Fix plan (incremental)

1. **Submissions:** Add get_queryset with tenant filter; add TenantResolvedAndStaff (or keep IsAuthenticated if student app uses it — check URL). If submissions are admin-only, TenantResolvedAndStaff is correct.
2. **Progress:** Add get_queryset to each ViewSet: lecture__tenant=request.tenant or session__lecture__tenant=request.tenant. Add TenantResolvedAndStaff so request.tenant is set.
3. **Schedule:** Add get_queryset: Dday.objects.filter(lecture__tenant=request.tenant). Add TenantResolvedAndStaff.
4. **Video:** VideoViewSet: override get_queryset to filter by session__lecture__tenant=request.tenant (and use select_related). Require TenantResolvedAndStaff or existing permission that implies tenant.

---

## Subsystem status

- [FIXED] submissions — get_queryset + create/OMR tenant; ExamSubmissionsList/OMR views
- [FIXED] progress — get_queryset tenant filter (via lecture/session)
- [FIXED] schedule — get_queryset tenant filter
- [FIXED] video — get_queryset tenant filter; delete_folder tenant scope
- [FIXED] exams — get_queryset + REGULAR create session tenant check; AnswerKey/Question/Sheet tenant-scoped
- [FIXED] homework_results — get_queryset tenant filter (Homework + HomeworkScore)
- [FIXED] results — SessionScoresView session resolution with lecture__tenant
