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

### 2.4 Video ASG 전용 스크립트 (삭제됨)
Video = Batch 전용으로 전환하면서 아래 스크립트는 **삭제**됨.  
(apply_video_asg_scaling_policy.ps1, video_worker_scaling_sqs_direct.ps1, redeploy_video_worker.ps1, verify_video_worker_ssm.ps1, video_worker_oneclick_setup.ps1, video_worker_oneclick_validate.ps1, apply_video_worker_scaling_fix.ps1, fix_video_worker_scaling_policy.ps1, remove_video_worker_target_tracking.ps1, verify_video_worker_scaling.ps1, investigate_video_asg_scalein.ps1, apply_video_mixed_instances.ps1, apply_video_target_tracking.ps1, update_video_tt_target.ps1, apply_video_visible_only_tt.ps1, collect_video_worker_incident_data.ps1, investigate_video_worker_runtime.ps1, diagnose_video_worker_full.ps1, diagnose_video_worker.ps1, check_backlog_metric.ps1)

### 2.5 ASG 관련 스크립트 (Video 제외 기본값)
- **redeploy_worker_asg.ps1**: `-ExcludeVideo` 기본 `$true` → video ASG 생성/업데이트 스킵.
- **deploy_worker_asg.ps1**: `-ExcludeVideo` 기본 `$true` → video LT/ASG 스킵.
- **check_all_worker_scaling_policies.ps1**: `-ExcludeVideo` 기본 `$true` → academy-video-worker-asg 스킵.
- **fix_all_worker_scaling_policies.ps1**: AI, Messaging ASG만 (Video 항목 제거됨).

---

## 3. 체크리스트 (배포 전 확인)

- [ ] API 배포 후 `check_api_batch_runtime.ps1` 자동 실행되어 PASS 확인.
- [ ] 신규 인프라 적용 시 `deploy_worker_asg.ps1` / `redeploy_worker_asg.ps1` 사용 (Video ASG 없음, AI/Messaging만).

이 문서는 배포 스크립트 점검 및 Video ASG 잔해 정리 결과를 정리한 것입니다.
