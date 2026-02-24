# Video 워커 Batch 전환 진행 플랜 (Cursor 기반)

**원칙:** 필요한 정보를 먼저 습득 → 원인/갭 확정 → 레거시(ASG/Lambda) 제거/격리 → Batch만 남기는 설계도 + 실행 순서.

**핵심:** Batch 워커가 제대로 안 뜨는 **원인을 먼저 고정**한 뒤, 공존 중인 ASG/Lambda/스크립트를 “삭제 or 비활성화 or 문서로 격리”해서 더 이상 흔들리지 않게 만든다.

**관련 문서:** [VIDEO_WORKER_BATCH_FULL_TRANSITION_REPORT.md](./VIDEO_WORKER_BATCH_FULL_TRANSITION_REPORT.md) (현황·잔존 코드·갭)

---

## Phase 0) 작업 원칙 확정

### Single Source of Truth(SSOT) 선언

- **Video 인코딩 실행 경로 = Batch only** 를 최상위 원칙으로 고정.
- ASG/Lambda 관련은 **(A) 즉시 제거 / (B) 기능 플래그로 완전 비활성화 / (C) 레거시 문서로 격리** 중 하나로만 남긴다.

### 안전장치

- “실서비스 영향 최소” 위해 정리 작업은 반드시 **관측(로그/메트릭) → 변경 → 검증 → 롤백 경로**를 같이 만든다.
- **정리는 Batch가 정상 동작한 뒤에 본격 제거.** (공존 때문에 어지럽지만, Batch가 안 뜨면 정리해도 계속 흔들림)

---

## Phase 1) Batch가 왜 안 뜨는지 “팩트 수집” (가장 먼저, 1~2시간)

Cursor에서 아래 4가지를 증거 기반으로 뽑으면 원인이 대개 1~2개로 좁혀진다.

### 1-1. “제출은 되고 있나?” (API → submit 성공 여부)

**대상:** `apps/support/video/services/batch_submit.py`, `create_job_and_submit_batch` 호출부

**확인 포인트:**

- `jobDefinition` / `jobQueue` 값이 실제로 무엇으로 들어가는지
- submit 결과(`jobId`) 저장/로그가 남는지
- 예외 발생 시 어디로 흘러가는지

**결과물:** “submit이 실패(예외)” vs “submit은 성공했는데 job이 RUNNING으로 안 감” 두 갈래로 분기.

### 1-2. “Job이 RUNNING으로 안 가는 이유” (AWS Batch 이벤트/상태의 전형 원인)

코드 확인과 함께, 계정에서 확인할 대표 원인:

- Job이 **SUBMITTED/RUNNABLE**에서 멈춤 → Compute Environment에 인스턴스가 안 붙음 / vCPU 부족 / AMI·Instance role 문제 / subnet·SG 문제
- Job이 **STARTING**에서 실패 → ECR pull 실패 / execution role / 네트워크 / 로그 권한
- 컨테이너가 바로 **FAILED** → entrypoint·command·환경변수·패키지 import 에러

**결과물:** “어느 상태에서 막히는지” + “실패 reason 문자열(Events)” 확보.

### 1-3. “워커가 뜨면 바로 죽는가?” (entrypoint → batch_main 흐름 검증)

**대상:**

- `apps/worker/video_worker/batch_entrypoint.py`
- `apps/worker/video_worker/batch_main.py`

**확인 포인트:** `process_video` 시작/종료 로깅, 예외 처리, exit code

**결과물:** 컨테이너가 실행은 되는데 앱이 죽는지, 아니면 컨테이너 자체가 못 뜨는지 분리.

### 1-4. “환경변수/리소스 이름이 코드·문서·실인프라에서 일치하나?”

- 코드 상수/설정: `VIDEO_BATCH_JOB_QUEUE`, `VIDEO_BATCH_JOB_DEFINITION`
- 문서/스크립트: `academy-video-batch-queue`, `academy-video-batch-jobdef`
- 한 글자라도 불일치하면 RUNNABLE에 박힐 수 있음.

**결과물:** 정확한 문자열 3종 세트 (Queue, JobDef, CE) + 실제 배포된 값 확인 필요 목록.

### Phase 1 목표

“Batch가 안 뜬다”를 **재현 가능한 한 문장**으로 만든다.  
예: “Job은 submit 되지만 RUNNABLE에서 멈춤(CE 인스턴스 0, instance role에 ECR 권한 없음)”

---

## Phase 2) “Batch 정상화”를 최우선으로 고치기 (원인별 처방 트랙)

Phase 1에서 나온 상태에 따라 트랙이 갈린다. 변경은 최소 단위로.

### 트랙 A: SUBMITTED/RUNNABLE 정체

- CE가 ENABLED인지 / maxvCpus 0으로 잠겨 있는지
- 서브넷/보안그룹 라우팅 (NAT 없이 ECR 접근 불가 등)
- Instance role / instance profile에 ECR pull + CloudWatch Logs + ECS/Batch 관련 정책 누락
- ARM64 AMI 선택했는데 이미지가 amd64만 있거나 반대 (아키텍처 불일치)

### 트랙 B: STARTING 실패

- execution role / logs 권한
- ECR 이미지 pull 권한
- Log group 생성/쓰기 권한

### 트랙 C: 컨테이너 즉시 FAILED

- entrypoint/command 잘못 지정 (JobDef의 command/entrypoint override)
- .env 필수값 누락
- python import path 문제 / packaging 문제

### Phase 2 결과물

“Batch 워커가 실제로 동작하는 가장 작은 성공 케이스” 확보.  
예: 더미 비디오 1건이 SUCCEEDED로 끝나고 로그가 `/aws/batch/academy-video-worker`에 남음.

---

## Phase 3) 레거시(ASG/Lambda/스크립트)를 “운영 영향 없이” 격리/비활성화 (Batch 성공 직후)

원칙: **운영 영향 없이** 격리. 삭제는 나중.

### 3-1. Lambda 2개는 비활성화가 1순위

- **queue_depth_lambda:** Video 메트릭 발행 분기 제거 또는 환경변수로 off
- **worker_autoscale_lambda:** Video 태그 기반 EC2 기동 분기 제거 또는 off  
  → 삭제는 나중. 먼저 “안 돌게” 만들면 운영 리스크가 급감.

### 3-2. Redis ASG interrupt + internal endpoint

- `redis_status_cache.py` + `VideoAsgInterruptStatusView`
- Lambda 제거가 끝나면 같이 제거.  
  지금은 “레거시 때문에 남아 있는 상태”로 표기만 유지.

### 3-3. delete_r2 Lambda 제거 (동기 R2 삭제로 전환)

- SQS `academy-video-delete-r2` + Lambda 제거.
- 영상 삭제 시 API에서 `delete_object_r2_video` / `delete_prefix_r2_video` 직접 호출(동기).

### 3-4. 스크립트/운영툴: “실행 불가능한 옵션 제거”

- **check_workers.py:** `sqs_main` import는 즉시 깨짐 → Batch 검증으로 바꾸거나 “video는 제외”
- **deploy.ps1, deploy_preflight.ps1, check_worker_docker.ps1, check_worker_logs.ps1:**  
  video 항목 제거 또는, `--video` 등이 들어오면 “Batch only, CloudWatch에서 확인”으로 즉시 종료해 오해/사고 방지

---

## Phase 4) 문서 SSOT 재작성 + 레거시 문서 격리

- **VIDEO_WORKER_SCALING_SSOT.md:** “현행(Batch)”만 남기고, ASG 내용은 “LEGACY” 섹션으로 접거나 별도 파일로 이동.
- **IAM 역할/정책:** Job Role / Execution Role / Instance Role 3개를 표로 정리하고, 어떤 스크립트가 무엇을 세팅하는지 연결.

---

## Phase 5) “완전 전환 완료” 정의 + 최종 삭제

### 완전 전환 완료 조건 (5개)

1. Batch 인코딩 job이 실데이터로 N건 연속 성공
2. stuck scan 재시도 경로가 정상 동작
3. Lambda 2개에서 Video 분기 0 (+ delete_r2 Lambda 제거)
4. 레거시 스크립트에서 video 옵션/설명 0 (또는 “Batch only”로 강제 종료)
5. Redis interrupt / internal API 제거 + 문서 업데이트

그 다음에 계정의 ASG / TargetTracking / 기타 리소스 삭제(있다면).

---

## Phase 1 Cursor 체크리스트 (팩트 수집용)

다음 단계에서 Cursor로 바로 할 수 있도록:

- **어떤 파일에서 어떤 키워드로 검색할지**
- **어떤 로그/예외 문구를 뽑아야 하는지**
- **“submit 성공/실패”를 코드 상으로 어떻게 판정할지**

를 정리한 **Phase 1 전용 체크리스트**를 별도로 붙일 수 있다. (필요 시 이 문서에 섹션 추가 또는 별도 파일로 작성.)
