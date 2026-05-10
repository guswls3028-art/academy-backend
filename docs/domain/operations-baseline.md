# V1.1.1 Operations Baseline

## 배포 경로 (사실 기준)

### CI 성공 = 운영 반영인 경우
- **API 서버 코드 변경** → `git push origin main` → CI smoke test → build → Deploy API (ASG refresh) → 운영 반영
- **Worker 코드 변경** → 동일 파이프라인, 변경된 워커만 selective deploy

### CI 성공 ≠ 운영 반영인 경우
- **ASG 설정 변경** (min/max/desired, scale-in protection) → `params.yaml` 수정 후 `deploy.ps1` 수동 실행 필요. CI/CD는 ASG 설정을 변경하지 않음.
- **Launch Template 변경** (UserData, AMI, instance type) → `deploy.ps1` 수동 실행 필요.
- **SkipMatching 이슈** → CI Deploy API가 SkipMatching=true 사용. 같은 launch template이면 인스턴스 미교체. Dockerfile만 변경 시 ECR 이미지는 바뀌지만 인스턴스가 같은 이미지를 캐시할 수 있음. 확실한 교체 필요 시: 수동 `start-instance-refresh --preferences '{"SkipMatching":false}'` 또는 인스턴스 terminate.

### 수동 운영 절차가 필요한 경우

| 상황 | 자동 (CI/CD) | 수동 필요 |
|------|------------|----------|
| 앱 코드 변경 | ✅ | — |
| DB 마이그레이션 | ✅ (SSM RunCommand) | — |
| ASG 설정 변경 | ❌ | `deploy.ps1` |
| 환경변수 추가 | ❌ | SSM Parameter Store 업데이트 + instance refresh |
| AWS 키 로테이션 | ❌ | AWS Console + `~/.aws/credentials` |
| ECR lifecycle 변경 | ❌ | `ecr-cleanup.py` 또는 Console |

## Requirements 운영 규칙

### 의존성 추가 시 수정할 파일
1. 해당 requirements 파일에 패키지 추가 (예: `requirements.txt`, `worker-ai-cpu.txt`)
2. 핵심 패키지면 `requirements/constraints.txt`에 버전 추가
3. 모든 Dockerfile에 `constraints.txt` COPY가 이미 있으므로 추가 작업 불필요

### constraints.txt 관리 원칙
- **핵심 12개 패키지만** pin (Django, DRF, boto3, psycopg2, pydantic, gunicorn, gevent, redis 등)
- AI/ML 패키지(torch, transformers)는 pin 하지 않음 (GPU/CPU 분리 빌드)
- 버전 업그레이드: constraints.txt 수정 → CI 통과 확인 → 운영 `pip show`로 실측 검증
- **운영 실측과 constraints 불일치 시**: 운영 버전을 기준으로 constraints 교정

## CI 파이프라인 구조

```
detect-changes → run-tests (7 smoke tests)
                      ↓
              build-and-push (ECR, latest + sha tag)
                      ↓
              run-migrations (SSM, API 인스턴스)
                      ↓
              deploy-api ← needs: build-and-push.result == 'success'
              deploy-messaging (변경 시만)
              deploy-ai (변경 시만)
                      ↓
              verify-deployment (healthz + health + ASG)
```

**테스트 실패 → 배포 차단**: run-tests 실패 → build-and-push skip → 모든 deploy skip.
증거: run `23158855514` (2026-03-17).

## 보안 현황

| 항목 | 상태 | 비고 |
|------|------|------|
| CI/CD 인증 | OIDC | 장기 키 미사용 |
| 운영 인스턴스 | IAM role | 장기 키 미사용 |
| 로컬 수동 작업 | `~/.aws/credentials` | **로테이션 필요** (git history 노출) |
| 레포 현재 파일 | 깨끗 | credential 없음 |
| git history | 노출됨 | `scripts/DEPLOY_COMMANDS.md` (삭제됨) 7회 |

## Observability 현황

| 항목 | 상태 | 파일 |
|------|------|------|
| Correlation ID | 운영 검증 완료 | `apps/api/common/correlation.py` |
| JSON logging | 운영 검증 완료 | `apps/api/common/logging_json.py` |
| Health endpoints | 3개 | `/healthz`, `/health`, `/readyz` |
| Sentry | 설치됨 | `sentry_sdk` in settings |
