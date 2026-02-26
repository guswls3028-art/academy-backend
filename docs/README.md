# docs — 문서 (SSOT 기준 구조)

**진입점은 이 파일 하나.** 루트에는 현재 유효한 문서만 두며, 과거/감사 문서는 `archive`, `03-REPORTS`로 격리한다.

```
docs/
├── README.md                   ← 지금 보고 있는 파일
│
├── 00-SSOT/                    현재 유효한 SSOT만
│   ├── ONE-TAKE-DEPLOYMENT.md   원테이크 멱등성 배포 설계
│   ├── RESOURCE-INVENTORY.md    리소스/이름/ARN/태그/환경별 값
│   ├── IDEMPOTENCY-RULES.md     멱등성 규칙·Wait 루프
│   ├── RUNBOOK.md               운영(배포·검증·장애·롤백·점검)
│   └── CHANGELOG.md             문서 기준 변경 로그
│
├── 01-ARCHITECTURE/             설명·설계·기준 문서
│   ├── 설계.md
│   ├── 10K_기준.md, 30K_기준.md
│   ├── REFERENCE.md
│   ├── AI_BATCH_WORKER_VS_OPS.md, VIDEO_WORKER_*.md, VIDEO_BATCH_*.md
│   └── infra/ API_ENV_*, LAMBDA_*, INTERNAL_* 등
│
├── 02-OPERATIONS/               실제 운영 가이드
│   ├── 배포.md, 운영.md
│   ├── video_batch_production_runbook.md
│   ├── INFRA_VERIFICATION_SCRIPTS.md
│   ├── SSM_JSON_SCHEMA.md, EVENTBRIDGE_RULES_STATE_AND_FUTURE.md
│   ├── actual_state/            스크립트 생성 실제 상태 JSON
│   └── audit_reports/           감사 스크립트 출력
│
├── 03-REPORTS/                  감사·검증·포렌식 결과 (역사 기록)
│   └── *_REPORT.md, *_AUDIT.md, *_VERIFICATION.md, *_FACTUAL.md
│
└── archive/                    완전 과거 (deploy_legacy, video_legacy, cursor_legacy, SSOT_0217, SSOT_0218)
```

---

## 빠른 참조

| 목적 | 문서 |
|------|------|
| **배포/운영 SSOT** (원테이크·리소스·멱등성·런북) | [00-SSOT/ONE-TAKE-DEPLOYMENT.md](00-SSOT/ONE-TAKE-DEPLOYMENT.md) |
| 리소스 이름·ARN·환경별 값 | [00-SSOT/RESOURCE-INVENTORY.md](00-SSOT/RESOURCE-INVENTORY.md) |
| 운영 절차(배포·검증·롤백) | [00-SSOT/RUNBOOK.md](00-SSOT/RUNBOOK.md) |
| Video Batch 상세 런북·환경 변수 | [02-OPERATIONS/video_batch_production_runbook.md](02-OPERATIONS/video_batch_production_runbook.md) |
| EventBridge 규칙 상태·향후 조치 | [02-OPERATIONS/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md](02-OPERATIONS/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md) |
| 인프라 검증 스크립트 정리 | [02-OPERATIONS/INFRA_VERIFICATION_SCRIPTS.md](02-OPERATIONS/INFRA_VERIFICATION_SCRIPTS.md) |
| 스크립트 폴더·용도 | [scripts/README.md](../scripts/README.md) |

저장소 최상위: [README.md](../README.md)
