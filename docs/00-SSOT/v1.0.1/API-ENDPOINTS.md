# V1.0.1 API Endpoints Map

**Snapshot Date:** 2026-03-11

---

## Health & System

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/healthz` | No | Liveness (ALB health check) |
| GET | `/health` | No | Readiness (DB check) |
| GET | `/readyz` | No | K8s-style readiness |

---

## Auth (`/api/v1/auth/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/login/` | No | JWT login (returns access+refresh) |
| POST | `/register/` | No | Student self-registration |
| POST | `/token/refresh/` | No | Refresh access token |
| POST | `/logout/` | Yes | Invalidate refresh token |
| GET | `/me/` | Yes | Current user info |
| GET | `/tenant-info/` | No | Tenant branding info |

---

## Students (`/api/v1/students/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | Admin | Student list (search, filter, paginate) |
| POST | `/` | Admin | Create student |
| GET | `/{id}/` | Admin | Student detail |
| PATCH | `/{id}/` | Admin | Update student |
| DELETE | `/{id}/` | Admin | Soft-delete student |
| POST | `/{id}/restore/` | Admin | Restore deleted student |
| POST | `/bulk-upload/` | Admin | Excel bulk import |
| GET | `/requests/` | Admin | Pending registration requests |
| POST | `/requests/{id}/approve/` | Admin | Approve request |
| POST | `/requests/{id}/reject/` | Admin | Reject request |

---

## Lectures (`/api/v1/lectures/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | Admin | Lecture list |
| POST | `/` | Admin | Create lecture |
| GET | `/{id}/` | Admin | Lecture detail |
| PATCH | `/{id}/` | Admin | Update lecture |
| DELETE | `/{id}/` | Admin | Delete lecture |
| POST | `/{id}/end/` | Admin | End lecture |
| POST | `/{id}/restore/` | Admin | Restore lecture |
| GET | `/{id}/sessions/` | Admin | Session list |
| POST | `/{id}/sessions/` | Admin | Create session |

---

## Sessions (`/api/v1/sessions/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/{id}/` | Admin | Session detail |
| PATCH | `/{id}/` | Admin | Update session |
| GET | `/{id}/attendance/` | Admin | Attendance records |
| PATCH | `/{id}/attendance/` | Admin | Update attendance |
| GET | `/{id}/scores/` | Admin | Score entries |
| PATCH | `/{id}/scores/` | Admin | Update scores |
| GET | `/{id}/videos/` | Admin | Session videos |

---

## Exams (`/api/v1/exams/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | Admin | Exam list (filter by session) |
| POST | `/` | Admin | Create exam |
| GET | `/{id}/` | Admin | Exam detail |
| PATCH | `/{id}/` | Admin | Update exam |
| DELETE | `/{id}/` | Admin | Delete exam |
| POST | `/{id}/generate-omr/` | Admin | Generate OMR PDF |
| POST | `/{id}/recalculate/` | Admin | Recalculate scores |
| GET | `/{id}/answer-key/` | Admin | Get answer key |
| POST | `/{id}/answer-key/` | Admin | Set answer key |
| GET | `/{id}/enrollments/` | Admin | Exam enrollments |

---

## Videos (`/api/v1/videos/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/sessions/{id}/videos/` | Admin | Session videos |
| POST | `/sessions/{id}/videos/` | Admin | Upload video |
| DELETE | `/{id}/` | Admin | Delete video |
| POST | `/{id}/retry/` | Admin | Retry processing |
| GET | `/public-session/` | Admin | Public video session |
| GET | `/folders/` | Admin | Video folders |
| POST | `/folders/` | Admin | Create folder |
| DELETE | `/folders/{id}/` | Admin | Delete folder |

---

## Student App (`/api/v1/student/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/me/` | Student | Profile |
| PATCH | `/me/` | Student | Update profile |
| GET | `/dashboard/` | Student | Dashboard data |
| GET | `/sessions/me/` | Student | My sessions |
| GET | `/exams/` | Student | Available exams |
| GET | `/exams/{id}/` | Student | Exam detail |
| POST | `/exams/{id}/submit/` | Student | Submit exam |
| GET | `/exams/{id}/result/` | Student | Exam result |
| GET | `/grades/summary/` | Student | Grades summary |
| GET | `/video/me/` | Student | Video home data |
| GET | `/video/sessions/{id}/` | Student | Session videos |
| GET | `/notifications/` | Student | Notifications |
| GET | `/notifications/counts/` | Student | Notification counts |
| GET | `/community/posts/` | Student | Community posts |
| POST | `/community/posts/` | Student | Create post |
| GET | `/clinic/bookings/` | Student | My clinic bookings |
| POST | `/clinic/bookings/` | Student | Create booking |
| DELETE | `/clinic/bookings/{id}/` | Student | Cancel booking |

---

## Messaging (`/api/v1/messaging/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/send/` | Admin | Send message |
| GET | `/history/` | Admin | Send history |
| GET | `/templates/` | Admin | Message templates |
| PATCH | `/auto-send/` | Admin | Auto-send settings |

---

## Community (`/api/v1/community/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/posts/` | Auth | Post list (type filter) |
| POST | `/posts/` | Auth | Create post |
| GET | `/posts/{id}/` | Auth | Post detail |
| PATCH | `/posts/{id}/` | Auth | Update post |
| DELETE | `/posts/{id}/` | Auth | Delete post |
| POST | `/posts/{id}/answers/` | Admin | Add answer |
| GET | `/notices/` | Auth | Notice list |

---

*Note: This is a representative subset. Full API spec is in the DRF browsable API at `/api/v1/`.*
