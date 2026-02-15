# cursor_docs — 배포·Docker·검증 (최소 구성)

**규칙**: AI(에이전트) 신규 문서는 이 디렉터리만 사용. **SSOT는 `docs/SSOT_0215/`** (건드리지 않음).

---

## 문서 인덱스

| 문서 | 용도 |
|------|------|
| [500_DEPLOY_CHECKLIST.md](500_DEPLOY_CHECKLIST.md) | 500 배포 전 체크리스트 (Gate 10, Docker 빌드 순서, AWS·env) |
| [PHASE2_RUNTIME_GATE_REPORT.md](PHASE2_RUNTIME_GATE_REPORT.md) | Gate 10 런타임 테스트 (7단계, 실행 방법·판정 [GO]) |
| [AWS_500_DOCKER_REQUIREMENTS_ALIGNMENT.md](AWS_500_DOCKER_REQUIREMENTS_ALIGNMENT.md) | Dockerfile·이미지·requirements·CMD와 가이드 기계 정렬 |
| [DOCKER_OPTIMIZATION_ANALYSIS_GUIDE.md](DOCKER_OPTIMIZATION_ANALYSIS_GUIDE.md) | Docker 최적화 분석 (베이스·레이어·non-root·.dockerignore) |
| [DOCKER_STRUCTURE_ALIGNMENT_REPORT.md](DOCKER_STRUCTURE_ALIGNMENT_REPORT.md) | 이상적 Docker 구조 물리 반영 검증 보고 |

---

## SSOT (참조만)

- **500 배포 따라하기**: `docs/SSOT_0215/AWS_500_START_DEPLOY_GUIDE.md`
- **10K 실행 계획·코드 정렬**: `docs/SSOT_0215/` 내 HEXAGONAL_10K_EXECUTION_PLAN_v1.5.md, CODE_ALIGNED_SSOT.md
