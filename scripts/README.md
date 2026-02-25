# scripts — 스크립트 폴더 트리 및 용도

**진입점은 이 파일.** 인프라·배포·진단 스크립트 위치와 용도를 한눈에 본다.

---

## 폴더 트리

```
scripts/
├── README.md                    ← 지금 보고 있는 파일
├── set_aws_env.ps1              AWS 자격증명/리전 설정 헬퍼
│
├── infra/                       인프라 설정·검증 (Batch, EventBridge, SSM, CloudWatch, IAM)
│   ├── batch/                   Batch JSON 템플릿
│   │   ├── video_compute_env.json
│   │   ├── video_job_queue.json
│   │   ├── video_job_definition.json
│   │   ├── ops_compute_env.json
│   │   ├── ops_job_queue.json
│   │   └── video_ops_job_definition*.json
│   ├── eventbridge/             EventBridge 규칙·타깃 JSON
│   │   ├── reconcile_to_batch_target.json
│   │   ├── scan_stuck_to_batch_target.json
│   │   └── *_schedule.json
│   ├── cloudwatch/              알람 JSON
│   ├── iam/                     IAM 정책·Trust JSON
│   │
│   ├── reconcile_video_batch_production.ps1   프로덕션 정합 (CE/큐/JobDef/EventBridge/알람)
│   ├── one_shot_video_ce_final.ps1            One-shot: 꼬인 Video CE 정리 후 단일 CE(final)로 큐 고정
│   ├── verify_video_batch_ssot.ps1            Video/Ops SSOT 검증 + evidence
│   ├── verify_video_batch_ssot.py             ECR/CloudWatch 보조 검증
│   ├── batch_ops_setup.ps1                    Ops CE/큐/JobDef 생성
│   ├── batch_video_setup.ps1                  Video CE/큐/JobDef (기본)
│   ├── batch_video_setup_full.ps1
│   ├── batch_video_verify_and_register.ps1     JobDef 검증·등록 (retry 1 등)
│   ├── batch_video_fix_memory_and_verify.ps1
│   ├── batch_video_ce_horizontal_scale.ps1
│   ├── recreate_batch_in_api_vpc.ps1          Batch를 API VPC에 생성·재생성
│   ├── eventbridge_deploy_video_scheduler.ps1 reconcile/scan_stuck 규칙·타깃 배포
│   ├── verify_eventbridge_wiring.ps1          EventBridge 규칙·타깃 검증
│   ├── ssm_bootstrap_video_worker.ps1         .env → SSM /academy/workers/env
│   ├── ssm_dump_video_worker_env.ps1
│   ├── cloudwatch_deploy_video_alarms.ps1      Video 큐 알람 배포
│   ├── run_netprobe_job.ps1                    Ops 큐에 netprobe job 제출
│   ├── production_done_check.ps1               Video 원테이크 검증
│   ├── infra_one_take_full_audit.ps1           전체 감사 (Video/Ops/EventBridge/IAM), -FixMode
│   ├── discover_api_network.ps1                API Private IP 등 → api_instance.json
│   ├── discover_batch_state.ps1
│   ├── discover_rds_network.ps1
│   ├── verify_batch_network_connectivity.ps1
│   ├── verify_ssm_env_shape.ps1
│   ├── iam_attach_batch_describe_jobs.ps1
│   └── … (validate_*, batch_ensure_*, batch_cleanup_*, ecr_bootstrap, network_minimal_bootstrap 등)
│
├── fix_batch_runnable_orphan_one_take.ps1      RUNNABLE orphan 일괄 정리
├── diagnose_batch_deep.ps1                     Batch 상태 심층 진단
├── diagnose_batch_worker.ps1
├── diagnose_batch_video_infra.ps1
├── verify_batch_terminate.ps1
├── apply_api_batch_submit_policy.ps1
│
├── build_and_push_ecr_remote.ps1               ECR 이미지 빌드·푸시 (Video 워커 등)
├── build_and_push_ecr.ps1
├── full_redeploy.ps1
├── deploy_preflight.ps1
├── deploy_worker_asg.ps1
├── redeploy_worker_asg.ps1
├── deploy_queue_depth_lambda.ps1
├── deploy_worker_autoscale.ps1
│
├── check_worker_docker.ps1
├── check_worker_logs.ps1
├── check_api_env.ps1
├── check_api_batch_runtime.ps1
├── check_redis_preconditions.ps1
└── … (기타 check_*, add_*, upload_*, quick_* 등)
```

---

## 용도별 빠른 참조

| 용도 | 스크립트 |
|------|----------|
| **프로덕션 인프라 정합** (CE/큐/JobDef/EventBridge/알람, 멱등) | `infra/reconcile_video_batch_production.ps1` |
| **One-shot Video CE 정리** (꼬인 CE 정지 후 단일 final CE로 큐 고정) | `infra/one_shot_video_ce_final.ps1` |
| **Video/Ops SSOT 검증** | `infra/verify_video_batch_ssot.ps1` |
| **전체 감사 + 수정** | `infra/infra_one_take_full_audit.ps1` (-FixMode) |
| **원테이크 순서** | `docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md` |
| **EventBridge 규칙 상태·향후 조치** | `docs/deploy/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md` |

---

## 관련 문서

- [docs/README.md](../docs/README.md) — 문서 폴더 트리
- [docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md](../docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md) — 인프라 원테이크 순서(SSOT)
- [docs/INFRA_VERIFICATION_SCRIPTS.md](../docs/INFRA_VERIFICATION_SCRIPTS.md) — 검증 스크립트 상세
