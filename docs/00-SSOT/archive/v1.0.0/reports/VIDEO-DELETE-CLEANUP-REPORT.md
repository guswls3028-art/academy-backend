# 영상 삭제 인프라 정리 체크 리포트

## 요약

프론트에서 영상 삭제 시 **R2, 큐, DB, Redis, DynamoDB** 등 모든 인프라에서 깔끔하게 제거되는지 검증함.

---

## 삭제 흐름 (DELETE /api/.../videos/{id}/)

| 단계 | 대상 | 처리 | 코드 위치 |
|------|------|------|-----------|
| 1 | AWS Batch Job | `terminate_batch_job()` 호출 (진행 중인 경우) | video_views.py, batch_control |
| 2 | VideoTranscodeJob | `job_mark_dead_if_active()` → DEAD | repositories_video.py |
| 3 | DynamoDB Lock | `lock_release(video_id)` | job_mark_dead_if_active 내부 |
| 4 | Redis Progress | `delete_video_progress_key()` | job_mark_dead_if_active 내부 |
| 5 | Video | `super().perform_destroy()` → DB 삭제 | video_views.py |
| 6 | VideoTranscodeJob | CASCADE 삭제 (Video FK) | Django ORM |
| 7 | VideoAccess, VideoProgress 등 | CASCADE 삭제 | Django ORM |
| 8 | R2 raw | `delete_object_r2_video(file_key)` | video_views.py (동기) |
| 9 | R2 HLS | `delete_prefix_r2_video(hls_prefix)` | video_views.py (동기) |

---

## 인프라별 정리 상태

### ✅ DB (PostgreSQL)
- **Video**: 삭제됨
- **VideoTranscodeJob**: CASCADE 또는 job_mark_dead_if_active로 DEAD 후 CASCADE
- **VideoAccess, VideoProgress, VideoPlaybackSession, VideoPlaybackEvent**: CASCADE
- **VideoOpsEvent**: video_id는 FK 아님(정수 필드) → 감사용으로 유지 (의도적)

### ✅ R2 (Cloudflare)
- **raw 원본**: `delete_object_r2_video(key=file_key)` 동기 삭제
- **HLS 출력**: `delete_prefix_r2_video(prefix=hls_prefix)` 동기 삭제 (prefix 아래 전체)
- **수정**: SQS/Lambda 미배포로 R2 미삭제 문제 → **동기 삭제로 전환** (video_views.py)

### ✅ Redis
- **진행률**: `delete_video_progress_key(tenant_id, video_id)` (job_mark_dead_if_active 시)
- **status/heartbeat**: TTL로 자동 만료 (명시적 삭제 없음)

### ✅ DynamoDB (video-job-lock)
- **락**: `lock_release(video_id)` → DeleteItem (job_mark_dead_if_active 시)

### ✅ AWS Batch
- **Job**: `terminate_batch_job()` 호출 (best-effort, 실패해도 삭제 진행)
- **Queue**: 인코딩은 Batch SubmitJob 직접 호출, SQS 미사용 → 별도 큐 없음

### ⚠️ SQS academy-video-delete-r2
- **상태**: 프로덕션에 **미배포** (큐·Lambda 없음)
- **조치**: R2 삭제를 **동기**로 전환하여 해당 인프라 의존성 제거

---

## 워커 측 정리 (진행 중 영상 삭제 시)

- `_video_still_exists()` False → `WORKER_CANCELLED_BY_VIDEO_DELETE` 로그 후 exit(0)
- `job_complete` 미호출 → DB/Redis 추가 갱신 없음
- **R2 raw**: 워커가 `delete_object_r2_video` 호출 (batch_main.py L213-219) — 완료 시 raw 삭제
- API `perform_destroy`에서도 raw/HLS 동기 삭제 → 이중 삭제 시도 가능하나, delete_object/prefix는 멱등

---

## 결론

| 인프라 | 정리 여부 | 비고 |
|--------|-----------|------|
| DB | ✅ | CASCADE + job_mark_dead |
| R2 | ✅ | 동기 삭제로 전환 완료 |
| Redis | ✅ | progress 키 삭제, 나머지 TTL |
| DynamoDB | ✅ | lock_release |
| Batch | ✅ | terminate 호출 |
| SQS | N/A | R2 삭제 경로에서 제거 |

**프론트에서 영상 삭제 시 위 인프라에서 모두 정리되도록 수정 반영됨.**
