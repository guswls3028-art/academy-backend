# docs — 문서 (폴더 트리)

**진입점은 이 파일 하나.** 아래 트리만 보면 된다.

```
docs/
├── README.md              ← 지금 보고 있는 파일
├── 배포.md
├── 운영.md
├── 설계.md
├── 10K_기준.md
├── 30K_기준.md
├── REFERENCE.md           개발·Cursor 참조 (Core, API, 규칙, 프론트 계약)
│
├── ai/                    AI/GPT 맥락 전달용
│   └── AI_HANDOFF_CONTEXT.md
│
├── video/
│   ├── batch/             Video Batch — 설계·검증·런칭·체크리스트
│   │   ├── VIDEO_BATCH_DESIGN_VERIFICATION_REPORT.md
│   │   ├── VIDEO_BATCH_SERVICE_LAUNCH_DESIGN_FOR_GPT.md
│   │   ├── VIDEO_BATCH_PRODUCTION_MINIMUM_CHECKLIST_AND_ROADMAP.md
│   │   ├── VIDEO_BATCH_SPOT_AND_INFRA_SAFETY_EVIDENCE_REPORT.md
│   │   ├── VIDEO_BATCH_PRODUCTION_READINESS_FORENSIC_AUDIT.md
│   │   └── … (나머지 VIDEO_BATCH_*.md)
│   │
│   ├── worker/            Video Worker — 아키텍처·스케일링·전환
│   │   ├── VIDEO_WORKER_README.md
│   │   ├── VIDEO_WORKER_ARCHITECTURE_BATCH.md
│   │   ├── VIDEO_WORKER_SCALING_SSOT.md
│   │   └── …
│   │
│   └── legacy/            레거시·과거 보고서 (Enterprise, SQS, B1, ASG 등)
│       └── VIDEO_ENTERPRISE_*.md, B1_*.md, …
│
├── infra/                 API·Lambda·내부 API·VPC
│   └── API_ENV_*.md, LAMBDA_*.md, INTERNAL_*.md, …
│
├── deploy/                 배포·재배포·검증
│   └── FULL_REDEPLOY_*.md, DEPLOY_AND_ASG_*.md, …
│
└── archive/               과거 스냅샷 (SSOT_0217, cursor_legacy 등) — 참고용
```

저장소 최상위: [README.md](../README.md)
