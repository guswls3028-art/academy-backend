# docs — 문서 (SSOT v4 기준)

**진입점은 이 파일.** 정식 인프라 문서는 **00-SSOT/v4** 한 세트만 사용한다.

---

## 필독: 정식 문서

| 목적 | 경로 |
|------|------|
| **인프라 SSOT (정식)** | [00-SSOT/v4/SSOT.md](00-SSOT/v4/SSOT.md) |
| **배포·검증·런북** | [00-SSOT/v4/runbook.md](00-SSOT/v4/runbook.md) |
| **파라미터** | [00-SSOT/v4/params.yaml](00-SSOT/v4/params.yaml) |
| **00-SSOT 구조·아카이브** | [00-SSOT/README.md](00-SSOT/README.md) |

---

## 폴더 구조

```
docs/
├── README.md                   ← 지금 보고 있는 파일
├── 00-SSOT/
│   ├── README.md               v4 링크·구조·아카이브 설명
│   ├── v4/                     정식 SSOT (SSOT.md, params.yaml, runbook, reports 등)
│   ├── v3_archive/             v3 문서·증명 (참고용)
│   └── legacy_reports_archive/ 과거 리포트 (참고용)
├── 01-ARCHITECTURE/            설계·기준 문서
├── 02-OPERATIONS/              운영 가이드
├── 03-REPORTS/                 감사·검증 결과
└── archive/                    완전 과거
```

---

## 빠른 참조

| 목적 | 문서 |
|------|------|
| **00-SSOT/v4/SSOT.md** | [00-SSOT/v4/SSOT.md](00-SSOT/v4/SSOT.md), [00-SSOT/v4/runbook.md](00-SSOT/v4/runbook.md) |
| **리포트** | [00-SSOT/v4/reports/](00-SSOT/v4/reports/) — drift.latest.md, audit.latest.md, verify.latest.md, history/ |
| Video Batch 런북·환경 변수 | [02-OPERATIONS/video_batch_production_runbook.md](02-OPERATIONS/video_batch_production_runbook.md) |
| EventBridge 규칙 상태 | [02-OPERATIONS/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md](02-OPERATIONS/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md) |
| 인프라 검증 스크립트 | [02-OPERATIONS/INFRA_VERIFICATION_SCRIPTS.md](02-OPERATIONS/INFRA_VERIFICATION_SCRIPTS.md) |
| 스크립트 폴더·용도 | [scripts/README.md](../scripts/README.md) |

저장소 최상위: [README.md](../README.md)
