# docs — 프로젝트 문서 단일 진입점

> 이 파일이 모든 문서의 시작점입니다.

## 진실 우선순위 (충돌 시)

1. `scripts/v1/` 실행 코드
2. `.github/workflows/` CI 워크플로우
3. `00-SSOT/params.yaml` 파라미터
4. SSOT 문서

## 핵심 경로

| 용도 | 경로 |
|------|------|
| 실행 파라미터 SSOT | [00-SSOT/params.yaml](00-SSOT/params.yaml) |
| 배포 아키텍처 | [00-SSOT/v1.1.0/DEPLOYMENT-ARCHITECTURE.md](00-SSOT/v1.1.0/DEPLOYMENT-ARCHITECTURE.md) |
| 배포 스크립트 | [../scripts/v1/deploy.ps1](../scripts/v1/deploy.ps1) |
| 검증 스크립트 | [../scripts/v1/verify.ps1](../scripts/v1/verify.ps1) |

## 폴더 구조

```
backend/docs/
├── README.md                         ← 현재 파일 (진입점)
│
├── 00-SSOT/                          ← 버전별 진실 문서
│   ├── params.yaml                   ← 실행 SSOT 파라미터 (스크립트가 직접 로드)
│   ├── IDENTIFIER-SSOT.md            ← ID 체계 SSOT
│   ├── messaging-policy.md           ← 메시징 정책
│   ├── PATH-ALIAS-POLICY.md          ← 경로 별칭 정책
│   ├── v1.1.0/                       ← 인프라/배포 기준 (현행)
│   │   ├── DEPLOYMENT-ARCHITECTURE.md
│   │   ├── INFRASTRUCTURE-OPTIMIZATION.md
│   │   ├── RELEASE-NOTES.md
│   │   └── RUNBOOK-*.md              ← 운영 런북 4종
│   ├── v1.1.1/                       ← 기능/도메인 SSOT (현행)
│   │   ├── RELEASE-NOTES.md
│   │   ├── messaging-ssot.md
│   │   ├── OMR-SYSTEM.md
│   │   ├── STATE-TRANSITION-SSOT.md
│   │   └── ... (도메인별 SSOT)
│   ├── reports/                      ← CI/스크립트 산출물
│   ├── scripts/                      ← SSOT 관련 스크립트
│   └── archive/                      ← 구버전 (참고용, 수정 금지)
│
├── 01-ARCHITECTURE/                  ← 설계 결정 기록
│   ├── 설계.md                       ← 전체 설계 개요
│   ├── REFERENCE.md                  ← 참고 자료
│   ├── INTERNAL_API_ALLOW_IPS.md     ← API 허용 IP
│   └── adr/                          ← Architecture Decision Records
│       ├── ADR-001 ~ ADR-004
│       └── admin API 계약서
│
└── 02-OPERATIONS/                    ← 운영 실무 가이드
    ├── 배포.md                       ← 배포 절차
    ├── 운영.md                       ← 운영 절차
    ├── DEPLOYMENT-MODES.md           ← 배포 모드
    ├── FORMAL-DEPLOY.md              ← 정식 배포 프로세스
    ├── local-dev-db.md               ← 로컬 DB 설정
    ├── SSM_JSON_SCHEMA.md            ← SSM 파라미터 스키마
    ├── video_batch_production_runbook.md ← 영상 배치 런북
    ├── 새-테넌트-커스텀-도메인-추가-메뉴얼.md
    ├── SSWE-테넌트-셋업-체크리스트.md
    └── 테넌트-도메인-가비아-네임서버.md
```

## 정리 기준

- **일회성 조사/검증 보고서:** 패치노트 반영 후 삭제. git history에서 조회 가능.
- **archive:** 봉인된 구버전만 보관. 운영 기준으로 사용 금지.
- **새 문서 작성 시:** 반드시 위 3개 폴더 중 하나에 배치. 루트에 직접 생성 금지.
