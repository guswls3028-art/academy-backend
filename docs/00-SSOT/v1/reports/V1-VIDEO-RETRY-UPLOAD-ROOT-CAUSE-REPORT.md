# V1 비디오 Retry / 업로드 파이프라인 근본 원인 및 수정 보고서

**갱신:** 2026-03-11  
**목적:** Retry API 409/400 실패 원인 규명, 5파일 동시 업로드 검증, 수정 적용 및 배포 정합 확인.

---

## 1. 실제 근본 원인

### 1.1 Retry 409 (video 219 — "업로드된 파일을 찾을 수 없습니다")

| 항목 | 내용 |
|------|------|
| **원인** | 비디오 상태가 **PENDING**이고 **file_key**는 있으나, R2에 해당 키의 **원본 객체가 없거나 크기가 0**인 경우. |
| **흐름** | Retry → `status == PENDING` and `file_key` → `_upload_complete_impl(video)` 호출 → `head_object(video.file_key)` → exists=False 또는 size=0 → **409**, detail "S3 object not found". |
| **가능한 상황** | (1) 5개 동시 업로드 시 한 건의 R2 PUT이 실패/미완료(네트워크·타임아웃) (2) 브라우저 탭 종료로 PUT 미완료 (3) presigned URL 만료 후 PUT 시도 실패. |
| **증거** | `video_views.py` L405–419: `_upload_complete_impl`에서 `head_object` 후 `not exists or size == 0`이면 409 반환. 프론트 `videos.ts` L24–26: "s3 object not found" → "업로드된 파일을 찾을 수 없습니다. 삭제 후 다시 업로드해 주세요." 매핑. |

### 1.2 Retry 400 (video 215)

| 항목 | 내용 |
|------|------|
| **가능한 원인** | (1) **PENDING**이지만 **file_key가 비어 있음** → ValidationError "업로드가 완료되지 않았습니다. 파일을 먼저 업로드해 주세요." (2) **Already in backlog** — 현재 Job이 QUEUED 또는 RETRY_WAIT이고 `updated_at`이 최근이라 재등록 거부 (3) **Cannot retry: status must be READY or FAILED** — status가 READY/FAILED/UPLOADED/PROCESSING이 아닌 예외 상태(드물음). |
| **증거** | `video_views.py` L545–547: PENDING이고 `not video.file_key`면 ValidationError 400. L579: QUEUED/RETRY_WAIT이고 최근이면 ValidationError "Already in backlog". L595–597: status가 READY/FAILED/UPLOADED/PROCESSING이 아니면 ValidationError. |

**프로덕션에서 219/215 정확한 구분:**  
프로덕션 DB/SSM 접근이 가능한 환경에서 다음 명령으로 상태 확인 가능:

```bash
cd backend
python manage.py diagnose_video_retry 219 215
```

- `status`, `file_key` 유무, `current_job_id`, R2 `head_object` 결과가 출력됨.
- `file_key` 비어 있으면 → 400 "업로드가 완료되지 않았습니다" 또는 수정 후 "업로드된 파일 정보가 없습니다" 경로.
- `file_key` 있는데 R2 exists=False → 409 "S3 object not found" 경로.

---

## 2. 409 vs 400 요약

| HTTP | 조건 | 사용자 메시지(프론트 매핑) |
|------|------|----------------------------|
| **409** | PENDING + file_key 있는데 R2 객체 없음/크기 0 (또는 재등록 경로에서 동일 검사 실패) | "업로드된 파일을 찾을 수 없습니다. 삭제 후 다시 업로드해 주세요." |
| **409** | RUNNING Job 있음, cancel_requested 아님 | "Cannot retry: a job is currently RUNNING..." (프론트: 현재 상태에서는 재시도할 수 없음) |
| **400** | PENDING + file_key 없음 | "업로드가 완료되지 않았습니다. 파일을 먼저 업로드해 주세요." |
| **400** | Already in backlog (QUEUED/RETRY_WAIT, 최근) | "이미 처리 중이거나 대기 중입니다. 잠시 후 다시 시도해 주세요." |
| **400** | status가 READY/FAILED/UPLOADED/PROCESSING 아님 | "현재 상태에서는 재시도할 수 없습니다." |

---

## 3. 5파일 동시 업로드 — 검증 결과

| 항목 | 내용 |
|------|------|
| **구현** | `VideoUploadModal.tsx`: SLOT_COUNT=5, N개 파일 선택 시 **Promise.all**로 N번 `initVideoUpload` 병렬 호출 → 각각 고유 (videoId, uploadUrl, file_key) 확보 후 **Promise.allSettled**로 N번 `uploadFileToR2AndComplete` 병렬 실행. |
| **결론** | **5파일 동시 업로드는 설계상 정상 지원됨.** 파일별로 별도 Video row와 별도 presigned URL·file_key를 사용하며, PUT과 upload/complete가 파일별로 1:1 대응. |
| **실패 시나리오** | 한 파일의 R2 PUT이 실패하거나 미완료되면 해당 비디오만 PENDING + file_key 유지, upload/complete 미호출. 사용자가 Retry 시 `_upload_complete_impl` → head_object 실패 → 409. |
| **권장** | 모달 안내 문구 유지: "업로드 버튼을 누르면 우하단 진행 상황에서 업로드·처리 진행을 확인할 수 있습니다." / 실패한 항목은 재시도 또는 삭제 후 재업로드. |

---

## 4. 적용한 수정 사항

### 4.1 Backend — Retry 재등록 경로에서 소스 파일 존재 검사

- **파일:** `apps/support/video/views/video_views.py`
- **내용:**  
  READY/FAILED 재처리(re-enqueue) 시, **create_job_and_submit_batch** 전에 **head_object(video.file_key)** 수행.  
  - file_key 없음 → ValidationError "업로드된 파일 정보가 없습니다. 삭제 후 다시 업로드해 주세요."  
  - head_object 예외 → ValidationError "저장소 확인 중 오류가 발생했습니다. 잠시 후 다시 시도하세요."  
  - exists=False 또는 size=0 → **409**, detail "S3 object not found" (기존 upload_complete와 동일), `error_reason=source_not_found_or_empty` 저장.
- **효과:** 재등록 직후 Batch에서 소스 없음으로 실패하는 대신, API에서 즉시 409로 응답하여 동일한 사용자 메시지로 안내 가능.

### 4.2 Backend — 400 메시지에 현재 상태 포함

- **파일:** `apps/support/video/views/video_views.py`
- **내용:**  
  "Cannot retry: status must be READY or FAILED" 메시지에 **current: {video.status}** 추가.
- **효과:** 로그/디버깅 시 215와 같은 400 원인(status 불일치 vs backlog 등) 구분 용이.

### 4.3 Frontend — Retry 에러 메시지 매핑

- **파일:** `frontend/src/features/videos/api/videos.ts`
- **내용:**  
  "업로드된 파일 정보가 없습니다" / "파일 정보가 없습니다" → "업로드된 파일을 찾을 수 없습니다. 삭제 후 다시 업로드해 주세요." 매핑 추가.
- **효과:** 백엔드 수정과 동일한 사용자 안내 문구 유지.

### 4.4 진단용 Management Command 추가

- **파일:** `apps/support/video/management/commands/diagnose_video_retry.py`
- **용도:**  
  지정한 video_id(들)에 대해 status, file_key, current_job_id, R2 head_object 결과 출력.  
  프로덕션에서 `python manage.py diagnose_video_retry 219 215` 실행 시 409/400 원인 확인 가능.

---

## 5. 변경된 파일 목록

| 파일 | 변경 요약 |
|------|------------|
| `backend/apps/support/video/views/video_views.py` | Retry 재등록 전 head_object 검사, file_key 없을 때 ValidationError, 409 반환, status 메시지 보강 |
| `frontend/src/features/videos/api/videos.ts` | "업로드된 파일 정보가 없습니다" 매핑 추가 |
| `backend/apps/support/video/management/commands/diagnose_video_retry.py` | 신규: retry 실패 진단용 management command |
| `backend/docs/00-SSOT/v1/reports/V1-VIDEO-RETRY-UPLOAD-ROOT-CAUSE-REPORT.md` | 본 보고서 |

---

## 6. 배포/인프라 정합성

- **GitHub Actions:** `v1-build-and-push-latest.yml` — 5개 이미지 `:latest` 빌드·푸시 후 API ASG instance refresh.
- **V1 스크립트:** `scripts/v1/deploy.ps1` — SSOT 기반 Ensure, Sync env, API refresh. `params.yaml`의 `ecr.useLatestTag: true`, `ecr.immutableTagRequired: false`로 풀배포가 CI와 동일하게 `:latest` 사용.
- **정합 문서:** `docs/00-SSOT/v1/reports/INFRA-IMAGE-BUILD-DEPLOY-ALIGNMENT.md`, Runbook `RUNBOOK-DEPLOY-AND-ENV.md`.
- **이번 작업에서의 인프라 변경:** 없음. 기존 정합 유지.

---

## 7. 재배포 및 검증 절차

### 7.1 재배포

- **코드 반영:** 본 수정이 포함된 브랜치를 main에 머지하면 CI가 이미지 빌드·푸시·API instance refresh 수행.
- **또는 풀배포:**  
  `cd backend`  
  `pwsh -File scripts/v1/deploy.ps1 -AwsProfile default -SkipNetprobe -SkipApiSSMWait`  
  (필요 시 Runbook §2 참고.)

### 7.2 검증

1. **Retry 409 (기존 실패 케이스)**  
   - 비디오 219 또는 동일 조건(PENDING + file_key 있으나 R2 객체 없음)에서 Retry 재호출.  
   - 기대: **409**, 프론트 메시지 "업로드된 파일을 찾을 수 없습니다. 삭제 후 다시 업로드해 주세요."

2. **Retry 400 (215 또는 file_key 없음)**  
   - file_key가 비어 있는 PENDING 비디오에서 Retry.  
   - 기대: **400**, "업로드가 완료되지 않았습니다" 또는 "업로드된 파일 정보가 없습니다" 매핑 메시지.

3. **Retry 재등록 경로 (READY/FAILED)**  
   - 소스가 R2에 있는 READY/FAILED 비디오에서 Retry → 202 및 재처리 등록.  
   - 소스가 R2에서 삭제된 READY/FAILED 비디오에서 Retry → **409** "S3 object not found" / "업로드된 파일을 찾을 수 없습니다."

4. **5파일 동시 업로드**  
   - 5개 파일 선택 후 업로드 → 우하단 진행률에서 5건 모두 완료되는지 확인.  
   - 일부만 실패할 경우 해당 항목만 PENDING 유지, Retry 시 위 409/400 동작과 일치하는지 확인.

5. **진단 명령 (선택)**  
   - 프로덕션 DB 접근 가능 시:  
     `python manage.py diagnose_video_retry 219 215`  
   - status, file_key, R2 head_object 결과로 409/400 원인 재확인.

---

## 8. 남은 리스크·미검증 항목

| 항목 | 설명 |
|------|------|
| **219/215 실제 DB 상태** | 프로덕션 DB에서 `diagnose_video_retry 219 215` 미실행 시, 409/400이 위 시나리오 중 어느 것에 정확히 해당하는지는 추정 수준. |
| **R2 PUT 실패율** | 5파일 동시 업로드 시 네트워크/타임아웃으로 인한 PUT 실패가 얼마나 발생하는지는 모니터링·로그로만 확인 가능. |
| **재배포 후 E2E** | 수정 반영 후 실제 프로덕션에서 Retry·5파일 업로드 E2E 한 번 이상 수행 권장. |

---

## 9. 참고

- Retry 로직: `apps/support/video/views/video_views.py` — `retry` 액션, `_upload_complete_impl`.
- R2 검사: `libs/s3_client/client.py` — `head_object` (R2_VIDEO_BUCKET).
- 키 SSOT: `apps/core/r2_paths.py` — `video_raw_key`, `video_hls_prefix`.
- 배포 Runbook: `docs/00-SSOT/v1/RUNBOOK-DEPLOY-AND-ENV.md`.
- 이미지 정합: `docs/00-SSOT/v1/reports/INFRA-IMAGE-BUILD-DEPLOY-ALIGNMENT.md`.
