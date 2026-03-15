# Full-Stack Audit — Phase 2: Frontend Findings

**Status:** Review completed. No code changes required for tenant-scoping compatibility.

---

## 1. App structure and routes (confirmed)

- **Entry:** `src/main.tsx` → BrowserRouter, QueryProvider, ProgramProvider, AuthProvider, AppRouter.
- **Routes:** `/login/*` (AuthRouter), `/` (RootRedirect), `/student/*` (ProtectedRoute student/parent → StudentRouter), `/admin/*` (ProtectedRoute owner/admin/teacher/staff → AdminRouter), `/dev/*` (owner → DevAppRouter), `/error/tenant-required`.
- **Admin:** dashboard, students, lectures, sessions, ddays, attendance, scores, exams, assignments, videos, clinic, staff, settings, etc.
- **Student:** dashboard, video, sessions, exams, grades, profile, qna, notices, notifications, clinic, idcard.

---

## 2. API client and tenant header

- **Axios:** `src/shared/api/axios.ts` — baseURL `VITE_API_BASE_URL + "/api/v1"`, JWT Bearer, **X-Tenant-Code** from `getTenantCodeForApiRequest()`, 401 refresh retry.
- **Tenant resolution:** `src/shared/tenant/index.ts` — storage / env / hostname; used for API and UI (StudentLayout, Header, AsyncStatusBar tenantScope).

All API calls go through this client, so **X-Tenant-Code is sent on every request**. Backend tenant-scoping changes (Phase 1) do not require frontend changes; 403 only if tenant is missing (e.g. bypass path).

---

## 3. Endpoints used by frontend vs backend (relevant to Phase 1 fixes)

| Frontend usage | Backend route | Phase 1 change | Compatible? |
|----------------|---------------|----------------|-------------|
| `GET /results/admin/sessions/:id/scores/` | SessionScoresView | get_object_or_404(Session, id=..., lecture__tenant=tenant) | Yes (tenant from header) |
| `GET /homeworks/?session_id=` | HomeworkViewSet | get_queryset session__lecture__tenant | Yes |
| `GET/PATCH /homework/scores/` | HomeworkScoreViewSet | get_queryset session__lecture__tenant | Yes |
| `GET/POST /exams/`, `GET /exams/answer-keys/` | ExamViewSet, AnswerKeyViewSet | get_queryset tenant-scoped | Yes |
| `GET/POST/DELETE /media/videos/`, retry, stats, folders | VideoViewSet | get_queryset session__lecture__tenant; delete_folder tenant scope | Yes |
| Dday | `DdayViewSet` | get_queryset + perform_create tenant check | N/A — frontend ddays API is stub (throws "not implemented") |

No frontend contract changes or URL changes were required.

---

## 4. Findings (severity)

### None critical for tenant isolation

- **Tenant:** Consistently applied via axios interceptor and `getTenantCodeForApiRequest()`. Admin and student apps use the same client; role split is route/permission (ProtectedRoute allow).
- **Auth:** JWT in localStorage, refresh on 401, `AuthContext` with `user` (tenantRole). Permission-based UI (e.g. TenantInfoCard for owner) matches backend roles.
- **Loading/error:** EmptyState, useQuery isLoading/isFetching, getApiErrorMessage used in multiple features. No systematic gap identified.

### Low / note

- **Dday:** Frontend `src/features/lectures/api/ddays.ts` is a stub (fetchDdays returns [], createDday/deleteDday throw). Backend DdayViewSet is tenant-scoped and ready when frontend is implemented.
- **Video Thumbnail:** `VideoThumbnail.tsx` has a path rewrite `media/hls/videos/` → `media/hls/videos/default/videos/` — confirm backend HLS path contract if "default" is tenant-specific.
- **AsyncStatusBar / useWorkerJobPoller:** Filter by `tenantScope` so only current tenant’s jobs are shown; consistent with backend tenant isolation.

---

## 5. Subsystem status

- [VERIFIED] App structure and routes — no change needed
- [VERIFIED] API client and X-Tenant-Code — compatible with Phase 1 backend
- [VERIFIED] Session scores, homeworks, homework scores, exams, videos — no frontend change
- [NOTE] Dday — frontend stub; backend ready
- [NOTE] Video HLS path "default" — confirm with backend if tenant-specific

---

## 6. Recommended next (Phase 3+)

- Phase 3: Build explicit contract table (request/response fields, auth, pagination) for each frontend-used API.
- Re-run frontend typecheck/lint/build after any future API or tenant changes.
