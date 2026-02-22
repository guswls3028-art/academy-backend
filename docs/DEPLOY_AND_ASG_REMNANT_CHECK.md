# 배포 스크립트 점검 및 Video ASG 잔해 정리

## 1. 배포 스크립트 점검 결과 (full_redeploy.ps1)

### 정상 동작
- **VideoViaBatch** (기본 `$true`): video worker EC2/ASG 배포 스킵. academy-video-worker 이미지는 **Build 단계에서만** 빌드·ECR 푸시 (Batch Job Definition용).
- **DeployTarget=video** 이고 VideoViaBatch 일 때: worker SSH 대상에서 academy-video-worker 제외, `$workerList = @()` 로 비움. "Batch 전용. EC2/ASG 배포 없음" 메시지 출력.
- **WorkersViaASG** 사용 시: `$asgMap`에서 VideoViaBatch 이면 `academy-video-worker` 제거 → video ASG instance refresh 호출 안 함.
- **API 배포 후**: `scripts/check_api_batch_runtime.ps1` 실행으로 Batch 설정(VIDEO_BATCH_JOB_QUEUE, VIDEO_BATCH_JOB_DEFINITION) 런타임 검증. 실패 시 배포 중단.

### 요약
- 기본 실행(`-VideoViaBatch` 생략)이면 video는 **이미지 빌드/푸시 + API 배포 + Batch 런타임 검증**만 수행되고, video worker EC2/ASG 배포는 하지 않음.
- **추가 수정 불필요.**

---

## 2. ASG 방식 Video 워커 잔해 정리

### 2.1 애플리케이션 코드
- **인코딩 경로**: `create_job_and_submit_batch` 만 사용. `create_job_and_enqueue` / SQS 인코딩 큐 호출 없음.
- **video_views.py**: 실패 로그 메시지를 `create_job_and_submit_batch returned None` 으로 통일 (이번 점검에서 수정).
- **apps/worker/video_worker/**: `sqs_main.py` 없음 (이미 제거됨). `batch_main.py` + `batch_entrypoint.py` 만 사용.

### 2.2 설정
- **VIDEO_SQS_QUEUE_NAME**: API/worker 설정에서 제거됨 (encoding = Batch 전용). `.env.example` 에는 주석 처리 + DEPRECATED 표기 유지.

### 2.3 Redis (ASG 인터럽트)
- **redis_status_cache.py**: `VIDEO_ASG_INTERRUPT_KEY`, `set_asg_interrupt()`, `is_asg_interrupt()` 유지.
- **사용처**: `internal_views.py` 의 `/internal/video/backlog/` 등에서 `is_asg_interrupt()` 호출. Lambda `queue_depth` 가 BacklogCount 퍼블리시 시 이 플래그를 참조해 스킵할 수 있음.
- **판단**: Video ASG 미사용이어도 Lambda/내부 API가 남아 있을 수 있으므로 **삭제하지 않고 유지**. 향후 Lambda·내부 API 정리 시 함께 제거 가능.

### 2.4 DEPRECATED 스크립트 (실행 금지, 참고용 보관)
| 스크립트 | 비고 |
|----------|------|
| `scripts/infra/apply_video_asg_scaling_policy.ps1` | [DEPRECATED] Video = Batch 전용. ASG 스케일링 정책 적용 금지. |
| `scripts/video_worker_scaling_sqs_direct.ps1` | [DEPRECATED] Video ASG SQS 메트릭 직접 참조. |
| `scripts/redeploy/redeploy_video_worker.ps1` | [DEPRECATED] Video Worker ASG Instance Refresh 전용. |
| `scripts/verify_video_worker_ssm.ps1` | [DEPRECATED] Video Worker SSM 검증 (ASG 기반). |
| `scripts/video_worker_oneclick_setup.ps1` | [DEPRECATED] Video Worker 원큐 셋업 (ASG). |

위 스크립트는 모두 상단에 `[DEPRECATED] Video = AWS Batch 전용` 표기 있음. **full_redeploy.ps1 / redeploy_worker_asg.ps1 에서 호출하지 않음.**

### 2.5 ASG 관련 스크립트 (Video 제외 기본값)
- **redeploy_worker_asg.ps1**: `-ExcludeVideo` 기본 `$true` → video ASG 생성/업데이트 스킵.
- **deploy_worker_asg.ps1**: `-ExcludeVideo` 기본 `$true` → video LT/ASG 스킵.
- **check_all_worker_scaling_policies.ps1**: `-ExcludeVideo` 기본 `$true` → academy-video-worker-asg 스킵.
- **fix_all_worker_scaling_policies.ps1**: Video ASG 항목 있으나, ExcludeVideo 로 제외 가능.

---

## 3. 체크리스트 (배포 전 확인)

- [ ] `full_redeploy.ps1` 실행 시 `-VideoViaBatch` 생략 또는 `$true` 로 사용.
- [ ] API 배포 후 `check_api_batch_runtime.ps1` 자동 실행되어 PASS 확인.
- [ ] Video 인코딩용으로 `redeploy_video_worker.ps1`, `apply_video_asg_scaling_policy.ps1` 등 DEPRECATED 스크립트 실행하지 않음.
- [ ] 신규 인프라 적용 시 `deploy_worker_asg.ps1` / `redeploy_worker_asg.ps1` 사용 시 `-ExcludeVideo` 유지 (기본값 사용 권장).

이 문서는 배포 스크립트 점검 및 Video ASG 잔해 정리 결과를 정리한 것입니다.
