# docs — 문서 (폴더 트리)

**진입점은 이 파일 하나.** 아래 트리만 보면 된다.

```
docs/
├── README.md                 ← 지금 보고 있는 파일
├── REFERENCE.md              개발·Cursor 참조 (Core, API, 규칙, 프론트 계약)
├── 배포.md
├── 운영.md
├── 설계.md
├── 10K_기준.md
├── 30K_기준.md
│
├── ai/                        AI/GPT 맥락 전달용
│   └── AI_HANDOFF_CONTEXT.md
│
├── video/
│   ├── batch/                 Video Batch — 설계·검증·런칭·체크리스트
│   │   └── VIDEO_BATCH_*.md, …
│   ├── worker/                Video Worker — 아키텍처·스케일링·전환
│   │   └── VIDEO_WORKER_*.md, …
│   └── legacy/                레거시·과거 보고서 (Enterprise, SQS, B1, ASG 등)
│       └── VIDEO_ENTERPRISE_*.md, B1_*.md, …
│
├── infra/                     API·Lambda·내부 API·VPC
│   └── API_ENV_*.md, LAMBDA_*.md, INTERNAL_*.md, …
│
├── deploy/                    배포·재배포·검증·실제 상태
│   ├── VIDEO_INFRA_ONE_TAKE_ORDER.md   Video/Ops 인프라 원테이크 순서(SSOT)
│   ├── EVENTBRIDGE_RULES_STATE_AND_FUTURE.md   규칙 비활성화 기록·향후 조치
│   ├── actual_state/          스크립트 생성 실제 상태 JSON
│   │   ├── batch_final_state.json
│   │   ├── batch_ops_state.json
│   │   ├── api_instance.json
│   │   └── …
│   ├── audit_reports/         감사 스크립트 출력 (infra_audit_*.json)
│   └── FULL_REDEPLOY_*.md, PRODUCTION_ONE_TAKE_FINAL.md, …
│
└── archive/                  과거 스냅샷 — 참고용
    └── cursor_legacy/, …
```

---

## 빠른 참조

| 목적 | 문서 |
|------|------|
| Video/Ops Batch 인프라 순서·역할 구분 | [deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md](deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md) |
| EventBridge 규칙 비활성화·재활성화·삭제·업로드 인프라 검토 | [deploy/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md](deploy/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md) |
| 인프라 검증 스크립트 정리 | [INFRA_VERIFICATION_SCRIPTS.md](INFRA_VERIFICATION_SCRIPTS.md) |
| 스크립트 폴더·용도 | [scripts/README.md](../scripts/README.md) |

저장소 최상위: [README.md](../README.md)
