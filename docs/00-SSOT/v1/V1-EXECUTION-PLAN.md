# V1 실행 계획 (PHASE 0)

**명칭:** V1 통일 (V1.1 미사용). **SSOT:** docs/00-SSOT/v1/params.yaml.  
**전제:** 1인 운영, 장애 대응 10~60분, 치명 이슈 해결 전에는 최종 배포/레거시 제거/프론트 완전 연결 금지.

---

## 작업 단위 및 게이트

| Phase | 작업 단위(커밋/PR 권장) | 완료 조건(게이트) | 검증 스크립트 |
|-------|--------------------------|-------------------|----------------|
| **0** | 본 문서 작성 | 문서 존재 | - |
| **1.1** | API /health 복구 + API LT drift 해소 | deploy-verification: apiHealth OK(200), target healthy ≥1, API LT drift NoOp | run-deploy-verification.ps1 |
| **1.2** | Video ops CE INVALID → Recreate | evidence: opsCeStatus=VALID | run-deploy-verification.ps1 |
| **1.3** | EventBridge reconcile/scan-stuck 정책 확정 | 의도/정책이 SSOT 또는 문서에 명시됨, 필요 시 ENABLED | run-deploy-verification.ps1 |
| **1 종료** | PHASE 1 통합 검증 | FAIL 0, 다음 단계 진행 가능 | run-deploy-verification.ps1 |
| **2** | Video 파이프라인 보강 (3시간 영상 E2E) | 3시간 샘플 1건 E2E + READY 전환, 보고서 근거 | 수동 + 보고서 |
| **3** | SQS 워커 안정성 (DLQ, visibility, graceful, 멱등) | 테스트 메시지 consume 성공, DLQ 정책 검증 | 수동 + 스크립트 |
| **4** | Front + R2 + CDN 완전 연결 | 프론트 200, 정적 로딩, CORS/쿠키/CSRF 정상 | run-deploy-verification.ps1 + 수동 |
| **5** | 레거시/불필요 리소스 안전 삭제 | drift 정리, 서비스 영향 없음 | run-deploy-verification.ps1 |
| **6** | 최종 검증 + 최종 보고서 | FAIL 0, GO 또는 CONDITIONAL GO 판정 | run-deploy-verification.ps1 |

---

## PHASE 1 상세

### 1.1 API /health unreachable 해결

- **점검 순서 (SSOT 기준):**
  1. ALB DNS: `describe-load-balancers` → `academy-v1-api-alb` 존재 및 DNS 획득.
  2. Target Group health check path: SSOT `api.healthPath` = `/health` → TG 생성 시 `--health-check-path` 일치 여부 (Ensure-TargetGroup에서 이미 SSOT 사용).
  3. 컨테이너: UserData에서 `docker run -p 8000:8000` → 앱이 `0.0.0.0:8000` 리스닝 및 `/health` 200 반환 필요.
  4. SG: ALB → EC2 8000 인바운드 허용 (sg-app 등).
  5. UserData 실패 시: EC2 콘솔 → 인스턴스 → 사용자 데이터 로그 / cloud-init / docker logs 확인.

- **API Launch Template drift (NewVersion) 해소:**
  - `Ensure-API-LaunchTemplate`: AMI/SG/Profile/UserData SSOT 기준으로 새 LT 버전 생성 → `Ensure-API-ASG`에서 instance-refresh (MinHealthyPercentage=100, InstanceWarmup=300) → 새 인스턴스 healthy 후 구 인스턴스 교체.
  - 배포 시 `Ensure-ALB`가 `ApiBaseUrl` 설정 → `Ensure-API-Instance`에서 `/health` 200 대기.

- **게이트:** `run-deploy-verification.ps1` 실행 후 deploy-verification-latest.md에서 apiHealth OK(200), target healthy ≥1, drift.latest.md에서 API LT Action = NoOp.

### 1.2 Video ops compute environment INVALID → Recreate

- `Ensure-OpsCE`: status=INVALID 또는 instance type drift 시 → Ops Queue DISABLED → Ops CE DISABLED → delete → wait → create (SSOT: opsInstanceType, opsMaxvCpus) → wait VALID → Ops Queue ENABLED.
- **게이트:** audit.latest.md / evidence에서 opsCeStatus=VALID.

### 1.3 EventBridge reconcile/scan-stuck DISABLED 처리

- **정책:** 운영 자동 복구 목적이면 규칙을 ENABLED로 유지하고 SSOT/문서에 명시.
- **구현:** `resources/eventbridge.ps1`에서 규칙 존재 시에도 `put-rule --state ENABLED` 호출하여 수동 DISABLED 상태를 복구. params.yaml `eventBridge.reconcileState` / `scanStuckState` = ENABLED 명시.
- **게이트:** 의도/정책이 SSOT 또는 본 문서에 명확히 기록됨.

### PHASE 1 완료 후 필수

- `scripts/v1/run-deploy-verification.ps1` 실행 (에이전트는 run-with-env.ps1로 .env 로드 후 실행).
- reports 갱신: deploy-verification-latest.md, audit.latest.md, drift.latest.md, V1-FINAL-REPORT.md.
- **FAIL 0**일 때만 PHASE 2 진행.

---

## 실행 시 인증

- AWS/Cloudflare: Cursor 룰(.cursor/rules) 준수. 에이전트가 루트 `.env`를 열어 환경변수로 설정한 뒤 실행.
- 예: `pwsh -File scripts/v1/run-with-env.ps1 pwsh -File scripts/v1/deploy.ps1 -Env prod` (주의: `--` 구분자 없이, 첫 번째 인자부터 하위 명령·인자로 전달)
- 예: `pwsh -File scripts/v1/run-deploy-verification.ps1 -AwsProfile default`

## PHASE 1 로컬 실행 체크리스트 (치명 이슈 해결)

배포·검증은 에이전트 터미널이 아닌 **로컬 터미널**에서 아래 순서로 실행할 것을 권장한다.

1. **배포 (LT drift + Ops CE Recreate + EventBridge ENABLED)**  
   프로젝트 루트에서 `.env`에 AWS/Cloudflare 키가 있다면:
   ```powershell
   pwsh -File scripts/v1/run-with-env.ps1 pwsh -File scripts/v1/deploy.ps1 -Env prod
   ```
   또는 이미 환경변수에 인증이 있다면:
   ```powershell
   pwsh -File scripts/v1/deploy.ps1 -Env prod -AwsProfile default
   ```
   (필요 시 `-SkipNetprobe`로 시간 단축)

2. **검증 및 보고서 갱신**
   ```powershell
   pwsh -File scripts/v1/run-deploy-verification.ps1 -AwsProfile default
   ```

3. **게이트:** FAIL 0, apiHealth OK, opsCeStatus=VALID, API LT drift NoOp.

---

## PHASE 2 ~ 6 요약

| Phase | 목표 | 완료 기준 |
|-------|------|-----------|
| **2** | Video 파이프라인 보강 | 1영상=1Job=1워커, 유령데이터 방지(READY 전 미공개), multipart/resume, 3시간 샘플 1건 E2E |
| **3** | SQS 워커 안정성 | DLQ·visibility SSOT, graceful shutdown, 멱등 키, enqueue→consume 검증 |
| **4** | Front + R2 + CDN | app/api 도메인 라우팅, 캐시 정책, CORS/쿠키/CSRF, 배포 파이프라인 |
| **5** | 레거시 제거 | SSOT 인벤토리, 삭제 전 증거, 단계 삭제 후 verification |
| **6** | 최종 검증·보고서 | FAIL 0, GO/CONDITIONAL GO, V1-FINAL-REPORT.md·deploy-verification-latest.md 갱신 |

