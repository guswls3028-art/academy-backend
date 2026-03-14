# V1 Video 인프라 현황 및 3시간 영상 대응 정리

**작성일:** 2026-03-07  
**목적:** 현재 video 인프라·R2 연결 상태와 3시간 영상 다건 동시 업로드 대비 내용 비교·정리

---

## 1. 현재 인프라 구성

| 구분 | 리소스 | 상태 | 비고 |
|------|--------|------|------|
| **Batch Standard** | academy-v1-video-batch-ce | ENABLED, VALID | 3시간 이하, Spot 혼합, maxvCpus 40 |
| | academy-v1-video-batch-queue | ENABLED | |
| | academy-v1-video-batch-jobdef | rev 20 | 2 vCPU, 4096 MiB |
| **Batch Long** | academy-v1-video-batch-long-ce | ENABLED | 3시간 초과, On-Demand, maxvCpus 80 |
| | academy-v1-video-batch-long-queue | ENABLED | ensure-video-long.ps1로 생성 |
| | academy-v1-video-batch-long-jobdef | ACTIVE | jobTimeout 12h, rootVolume 300GB |
| **Ops** | academy-v1-video-ops-ce/queue | ENABLED | reconcile(15분), scanStuck(5분) |
| **EventBridge** | reconcile, scan-stuck | ENABLED | Batch SubmitJob 연결 |
| **DynamoDB** | academy-v1-video-job-lock | ACTIVE | 1 video 1 job 락 |
| **R2** | academy-video | OK | raw/HLS 저장, wrangler list 성공 |

---

## 2. R2 연결 흐름

| 구간 | 경로 | 용도 |
|------|------|------|
| **업로드** | 프론트 → presigned PUT → R2 | 원본(raw) 업로드 |
| **인코딩** | Batch Worker → R2 GET(raw) → ffmpeg → R2 PUT(HLS) | 인코딩·스테이징 |
| **재생** | CDN/r2.dev → R2 GET | HLS 스트리밍 |
| **삭제** | API → SQS → Delete Worker → R2 | 객체 삭제 |

**API SSM env:** `R2_ENDPOINT`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_VIDEO_BUCKET`, `CDN_HLS_BASE_URL`  
**Worker:** 동일 R2 설정 + `r2UploadPartSizeMb`, `r2UploadMaxConcurrency`, `r2UploadMaxAttempts` (params.yaml)

---

## 3. 3시간 영상 다건 대비 조치 요약

| 항목 | SSOT/설계 | 적용 상태 | 비고 |
|------|-----------|-----------|------|
| **Long 큐/CE/JobDef** | params videoBatch.long | ✅ 생성 | ensure-video-long.ps1 (params 파서 중첩 미지원) |
| **큐 라우팅** | duration ≥ 10800s → Long | ✅ 반영 | batch_submit.py |
| **SSM VIDEO_BATCH_JOB_QUEUE_LONG** | academy-v1-video-batch-long-queue | ✅ 수동 반영 | deploy.ps1에서 자동 설정 없음 |
| **SSM VIDEO_BATCH_JOB_DEFINITION_LONG** | academy-v1-video-batch-long-jobdef | ✅ 수동 반영 | |
| **VIDEO_TENANT_MAX_CONCURRENT** | 6 (테넌트당 동시 Job) | ✅ 6으로 상향 | 기본 2 → 5건 동시 업로드 대비 |
| **VIDEO_LONG_DURATION_THRESHOLD_SECONDS** | 10800 (3h) | ✅ | |
| **API Instance Refresh** | SSM 변경 반영 | ✅ 완료 | 5~10분 소요 |

---

## 4. SSOT vs 실제 비교

| 항목 | params.yaml (SSOT) | 실제/문서 | 일치 |
|------|-------------------|-----------|------|
| Standard CE maxvCpus | 40 | rca: 10 (drift) | ⚠️ |
| Standard instanceType | c6g.xlarge | rca: c6g.large | ⚠️ |
| Long CE/Queue/JobDef | 정의됨 | ensure-video-long.ps1로 생성 | ✅ |
| R2 bucket | academy-video (cursorrules) | e2e: academy-video | ✅ |
| Job timeout Long | 43200s (12h) | Long JobDef | ✅ |

---

## 5. 검증 체크리스트 (3시간 영상 5건 동시 업로드)

| # | 항목 | 확인 방법 |
|---|------|-----------|
| 1 | API healthz 200 | `curl -s -o NUL -w "%{http_code}" https://api.hakwonplus.com/healthz` |
| 2 | SSM VIDEO_BATCH* | `aws ssm get-parameter --name /academy/api/env --query Parameter.Value --output text \| Select-String VIDEO` |
| 3 | Long Queue 존재 | `aws batch describe-job-queues --job-queues academy-v1-video-batch-long-queue` |
| 4 | R2 접근 | `wrangler r2 bucket list` |
| 5 | 업로드 테스트 | hakwonplus.com/admin/lectures/{id}/sessions/{id}/videos |

---

## 6. 알려진 이슈·주의사항

- **params 파서:** `videoBatch.long` 중첩 구조 미지원 → Long 리소스는 `ensure-video-long.ps1`로 별도 생성
- **SSM 수동 반영:** Long 관련 env는 deploy.ps1에서 자동 설정되지 않음. SSM `/academy/api/env`에 `VIDEO_BATCH_JOB_QUEUE_LONG`, `VIDEO_BATCH_JOB_DEFINITION_LONG`, `VIDEO_TENANT_MAX_CONCURRENT` 수동 추가 필요
- **Instance Refresh:** SSM 변경 후 API Instance Refresh 실행해야 새 env 반영 (5~10분)
- **Standard CE Drift:** maxvCpus 10→40, instanceType c6g.large→c6g.xlarge 정렬 시 Ensure-VideoCE drift 처리

---

**관련 문서:** rca.video.latest.md, UPLOAD-TEST-READINESS.md, params.yaml (videoBatch)
