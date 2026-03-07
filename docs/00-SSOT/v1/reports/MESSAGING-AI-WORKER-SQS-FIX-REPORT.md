# 메시징/AI 워커 SQS 연결 수정 보고서

**일시:** 2026-03-07  
**목적:** 문자 발송(메시징 워커) 및 엑셀 학생 업로드(AI 워커) 미동작 원인 분석 및 수정

---

## 1. 원인 분석

### 1.1 SQS 큐 이름 불일치

| 구성요소 | 기본값(코드) | params SSOT | 실제 AWS 큐 |
|----------|-------------|-------------|-------------|
| 메시징 API enqueue | academy-messaging-jobs | academy-v1-messaging-queue | 둘 다 존재 |
| 메시징 워커 consume | academy-messaging-jobs | academy-v1-messaging-queue | 둘 다 존재 |
| AI API enqueue | academy-ai-jobs-basic | academy-v1-ai-queue | 둘 다 존재 |
| AI 워커 consume | academy-ai-jobs-lite, basic, premium | academy-v1-ai-queue | 둘 다 존재 |

**문제:** API와 워커가 서로 다른 큐를 사용할 경우 메시지가 전달되지 않음.

### 1.2 SSM 환경변수 누락

- `/academy/workers/env`: `MESSAGING_SQS_QUEUE_NAME`, `AI_SQS_QUEUE_NAME_*` 미설정 시 기본값 사용
- `/academy/api/env`: 동일 키 미설정 시 기본값 사용
- Bootstrap은 **신규 SSM 생성 시에만** SQS 주입 → 기존 배포는 수동 갱신 필요

---

## 2. 적용한 수정 사항

### 2.1 Bootstrap 수정 (`scripts/v1/core/bootstrap.ps1`)

- 신규 workers env 생성 시 params SSOT 큐 이름 자동 주입:
  - `MESSAGING_SQS_QUEUE_NAME` = academy-v1-messaging-queue
  - `AI_SQS_QUEUE_NAME_BASIC/LITE/PREMIUM` = academy-v1-ai-queue

### 2.2 SSM 갱신 스크립트 추가

| 스크립트 | 용도 |
|----------|------|
| `scripts/v1/update-workers-env-sqs.ps1` | `/academy/workers/env`에 SQS 큐 이름 주입 |
| `scripts/v1/update-api-env-sqs.ps1` | `/academy/api/env`에 SQS 큐 이름 주입 |

### 2.3 실행 결과 (2026-03-07)

```
✅ update-workers-env-sqs.ps1: SSM /academy/workers/env 갱신 완료
✅ update-api-env-sqs.ps1: SSM /academy/api/env 갱신 완료
```

---

## 3. 적용 완료 (에이전트 직접 수행)

### 3.1 instance-refresh 실행

| ASG | InstanceRefreshId | 상태 |
|-----|-------------------|------|
| academy-v1-api-asg | cf00aedd-505b-4fb4-9e18-c08a4e18c6a6 | **Successful** |
| academy-v1-messaging-worker-asg | 35ca3df1-b7f0-414c-bed0-b9162256077d | InProgress |
| academy-v1-ai-worker-asg | 08c2d560-8f0d-44da-acfa-0336ebfbfbdc | InProgress |

API ASG는 완료됨. 워커 ASG는 scale-in protection 등으로 인해 10~15분 소요 예상.

---

## 4. 검증 방법

### 4.1 메시징 (문자 발송)

1. 관리자 화면에서 학생 선택 후 문자 발송
2. SQS `academy-v1-messaging-queue` ApproximateNumberOfMessages 확인 (발송 시 일시 증가 후 워커가 소비)
3. Messaging 워커 CloudWatch 로그에서 `Messaging job enqueued` / Solapi 발송 로그 확인

### 4.2 AI (엑셀 학생 업로드)

1. 학생 관리 → 엑셀 일괄 등록 → 파일 업로드
2. SQS `academy-v1-ai-queue` ApproximateNumberOfMessages 확인
3. AI 워커 CloudWatch 로그에서 `EXCEL_PARSING` / `SQS_MESSAGE_RECEIVED` 확인
4. `GET /api/v1/students/excel_job_status/<job_id>/` 폴링으로 완료 여부 확인

---

## 5. 파이프라인 요약

| 단계 | 상태 |
|------|------|
| 배포 검증 (run-deploy-verification.ps1) | WARNING (API LT drift 1건, 기타 PASS) |
| SQS 큐 존재 | academy-v1-messaging-queue, academy-v1-ai-queue 존재 |
| DLQ 적재 | Messaging DLQ=0, AI DLQ=0 |
| SSM 갱신 | workers env, api env SQS 키 주입 완료 |
| instance-refresh | API Successful, Messaging/AI InProgress (완료 대기 중) |
