# 인프라·백엔드·프론트엔드 정합성 검사 및 정렬 보고서

**생성일:** 2026-03-07  
**범위:** API 서버, AI 워커, Messaging 워커, Video Batch, Video Ops, 문서·스크립트

> **도메인별 API 경로 정합성:** 엔드포인트 ↔ 프론트 호출 매칭 검사는 별도 문서 참고 → [DOMAIN-API-CONSISTENCY-REPORT.md](./DOMAIN-API-CONSISTENCY-REPORT.md)

## 1. 수행한 정합성 조치

### 1.1 SSOT (params.yaml) ↔ 스크립트 파서
- **문제:** 2-level YAML 파서가 `front.domains.app` / `front.domains.api` 및 `videoBatch.long` 중첩 값을 읽지 못함.
- **조치:**
  - `front`: flat 키 `domainsApp`, `domainsApi` 추가. `ssot.ps1`에서 flat 키 우선 읽기.
  - `videoBatch`: flat 키 `longComputeEnvironmentName`, `longQueueName`, `longWorkerJobDefName` 추가. `ssot.ps1`에서 `$vbl` 없을 때 flat 키로 Long CE/Queue/JobDef 설정.
- **결과:** 배포 검증·인벤토리에서 `front.domains.api` 및 Video Long 리소스가 SSOT와 일치.

### 1.2 SQS 큐 이름 (API · Messaging · AI)
- **SSOT:** `academy-v1-messaging-queue`, `academy-v1-ai-queue` (단일 큐).
- **조치:**
  - **API:** `base.py`, `worker.py` 기본값을 위 큐 이름으로 변경.
  - **Messaging 워커:** `config.py` 기본값 `academy-v1-messaging-queue`.
  - **AI:** `apps/support/ai/services/sqs_queue.py`, `apps/support/messaging/sqs_queue.py` 클래스 상수 및 fallback을 SSOT 이름으로 통일.
  - **SSM:** `update-api-env-sqs.ps1`, `update-workers-env-sqs.ps1` 실행으로 `/academy/api/env`, `/academy/workers/env`에 위 큐 이름 반영.
- **결과:** API enqueue ↔ 워커 소비 큐 일치. SSM 기반 배포 시에도 동일 이름 사용.

### 1.3 API 공개 URL (배포 검증 스크립트)
- **문제:** `FrontDomainApi`가 이미 `https://api.hakwonplus.com`일 때 `https://`를 한 번 더 붙여 `https://https://api.hakwonplus.com` 생성.
- **조치:** `run-deploy-verification.ps1`에서 `FrontDomainApi`에 `https://` 붙일 때, 이미 `http://`/`https://`로 시작하면 제외.
- **결과:** 공개 /health 검사 URL 정상.

### 1.4 Video Batch Long (인벤토리)
- **문제:** `run-resource-inventory.ps1`에서 Video Long CE/Queue가 LEGACY_CANDIDATE로 분류됨 (SSOT 파서가 long 블록 미파싱).
- **조치:** params에 flat 키 추가 + ssot에서 Long 이름 설정 → 인벤토리 시 `KeepBatchCE`/`KeepBatchQueue`에 Long 포함.
- **결과:** `academy-v1-video-batch-long-ce`, `academy-v1-video-batch-long-queue`가 KEEP으로 분류.

## 2. 서비스별 정합성 요약

| 서비스 | 인프라(SSOT) | 백엔드/워커 | 연결 |
|--------|----------------|-------------|------|
| API ASG | academy-v1-api-asg, academy-v1-api-lt | SSM /academy/api/env → Gunicorn | ALB → TG → API |
| Messaging 워커 | academy-v1-messaging-worker-asg | SSM /academy/workers/env, MESSAGING_SQS_QUEUE_NAME=academy-v1-messaging-queue | SQS academy-v1-messaging-queue |
| AI 워커 | academy-v1-ai-worker-asg | SSM /academy/workers/env, AI_SQS_QUEUE_NAME_*=academy-v1-ai-queue, API 폴링 | SQS academy-v1-ai-queue + API /internal/ai/job/next |
| Video Batch | academy-v1-video-batch-ce, academy-v1-video-batch-queue, academy-v1-video-batch-jobdef | base.py VIDEO_BATCH_* env 기본값 SSOT와 동일 | Batch submit ↔ CE/Queue |
| Video Batch Long | academy-v1-video-batch-long-ce/queue/jobdef | base.py VIDEO_BATCH_JOB_QUEUE_LONG 등 | 동일 |
| Video Ops | academy-v1-video-ops-ce, academy-v1-video-ops-queue, reconcile/scanstuck/netprobe JobDef | eventbridge.ps1, batch.ps1 SSOT | EventBridge → Ops Queue → JobDef |
| 프론트 | front.domains.api=https://api.hakwonplus.com | VITE_API_BASE_URL (env), 백엔드 CORS_ALLOWED_ORIGINS | 동일 |

## 3. 문서·스크립트 정합성

- **params.yaml:** front flat 키, videoBatch long flat 키 추가. 기존 중첩 블록은 유지(가독성).
- **ssot.ps1:** FrontDomainApi/App, VideoLongCEName/QueueName/JobDefName flat 키 반영.
- **run-deploy-verification.ps1:** API 공개 URL 생성 시 프로토콜 중복 방지.
- **update-api-env-sqs.ps1 / update-workers-env-sqs.ps1:** SSM에 SSOT 큐 이름 주입 (이미 실행 완료).

## 4. 배포 및 검증

- SSM SQS 갱신 완료. API/워커 인스턴스는 기존 SSM 값을 사용 중이면 이미 동일 큐 사용.
- 코드 기본값을 SSOT로 맞춰, SSM 미설정 시에도 동일 큐 사용.
- 배포 실행 시 API/워커 ASG instance-refresh 시 새 SSM·이미지 반영됨.

---
**정리:** 인프라(실제 AWS)·백엔드(API/워커 설정)·프론트(API URL)·문서·스크립트가 SSOT 기준으로 정렬되었으며, 모든 서비스가 동일한 큐·CE/Queue·도메인을 사용하도록 정합성 확보됨.
