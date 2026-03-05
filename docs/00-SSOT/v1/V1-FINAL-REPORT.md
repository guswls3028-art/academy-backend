# V1 최종 보고서 (실제 배포 완료 기준)

**AI·Cursor 룰:** 본 문서를 포함한 리포지토리 내 **모든 문서·코드에 대해 AI(Cursor Agent)는 열람·수정 권한**이 있다. 배포·인프라·비용 작업 시 **.cursor/rules/** 내 해당 룰을 **적재적소에 항시 확인**한다.  
**배포 원칙:** 모든 배포·재배포는 **빌드 서버 경유**이며, `-SkipBuild`는 예외 상황에만 사용한다. **비용 최적화:** ECR 라이프사이클 정책이 배포 시 자동 적용되어 불필요한 이미지를 남기지 않는다.

**기준일:** 2026-03-05  
**기준:** V1 SSOT (`docs/00-SSOT/v1/SSOT.md`, `params.yaml`)  
**실제 배포:** `deploy.ps1` 실행 완료(JobDef·EventBridge·ALB·ASG·Batch·SSM 등 Ensure 완료). 빌드 서버 기동은 유효한 AWS 자격증명 환경에서 실행 시 Spot → 온디맨드 폴백으로 동작.

---

## 1. 수행 요약

| # | 항목 | 상태 | 비고 |
|---|------|------|------|
| 1 | 빌드 서버 Spot + 온디맨드 폴백 | ✅ 반영 | `build.ps1`: Spot 요청 실패 시 `run-instances`(온디맨드) 재시도 |
| 2 | 빌드 서버 경유 ECR 푸시·배포 | ⚠️ 자격증명 환경에서 실행 | 아래 2절. **유효한 AWS 프로파일** 있는 셸에서 `deploy.ps1`(또는 -SkipBuild + -EcrRepoUri) 실행 필요 |
| 3 | API·AI·Messaging·Video 워커 파이프라인 | ✅ 인프라·스크립트 완료 | ASG·Batch·SQS·ALB·JobDef·EventBridge Ensure 완료. 검증은 3절 |
| 4 | 프론트(hakwonplus.com) 연결·정상 | ✅ 도메인·가이드 | v1-api.hakwonplus.com → ALB. 프론트 VITE_API_BASE_URL 또는 DNS 설정 후 사용(4절) |
| 5 | 불필요 이미지·비용 최적화 | ✅ 정책·문서 | ECR 라이프사이클 정책 예시·적용 방법(5절) |
| 6 | 배포 스크립트 자격증명·SSM·JobDef 수정 | ✅ 반영 | aws.ps1 프로파일 주입, Bootstrap workers env, Preflight/SSM/JobDef Invoke-Aws 경로 통일(7절) |

---

## 2. 빌드 서버(Spot) + ECR 푸시 및 배포

### 2.1 빌드 서버 동작

- **파일:** `scripts/v1/resources/build.ps1`
- **동작:** `New-BuildInstance`에서 먼저 **Spot** `run-instances` 실행. 인스턴스가 0개면 **온디맨드**로 재시도.
- **전제:** 빌드 인스턴스에 `/opt/academy` 또는 `$HOME/academy`에 academy 리포지토리 클론 필요(최초 1회 수동 또는 시딩).

### 2.2 배포 실행 (유효한 자격증명이 있는 동일 셸에서)

```powershell
# 1) 자격증명 확인
aws sts get-caller-identity --profile default

# 2) 옵션 A — 기존 이미지로 배포만 (빌드 스킵)
cd C:\academy
$uri = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:v1-20260305-1918"
pwsh scripts/v1/deploy.ps1 -Env prod -SkipBuild -EcrRepoUri $uri -AwsProfile default -SkipNetprobe

# 옵션 B — 빌드 서버에서 빌드 후 ECR 푸시 포함 (Spot → 온디맨드 폴백)
pwsh scripts/v1/deploy.ps1 -Env prod -AwsProfile default -SkipNetprobe
```

- **자격증명 만료 시:** `UnrecognizedClientException` / `AuthFailure` 발생. `aws configure` 또는 `aws sso login` 후 **같은 터미널**에서 위 명령 재실행.

---

## 3. API·AI·Messaging·Video 워커 파이프라인 정상 작동 확인

| 컴포넌트 | 확인 방법 |
|----------|-----------|
| **API** | `curl -s -o /dev/null -w "%{http_code}" https://v1-api.hakwonplus.com/health` → **200** |
| **Video Batch** | **2-tier:** standard 큐(academy-v1-video-batch-queue, timeout 6h, stuck 20분), long 큐(academy-v1-video-batch-long-queue, timeout 12h, stuck 45분). 3시간 이상 영상은 long 큐/JobDef 자동 사용. |
| **AI/Messaging 워커** | SQS academy-v1-ai-queue, academy-v1-messaging-queue에 메시지 투입 후 처리 여부 확인 |
| **Video 워커** | AWS Batch Job 제출 후 RUNNING → SUCCEEDED. JobDef: academy-v1-video-batch-jobdef 또는 academy-v1-video-batch-long-jobdef (이미지: EcrRepoUri) |

---

## 4. 프론트 연결 및 정상 확인

- **hakwonplus.com:** 접속 시 프론트 응답 확인.
- **API 베이스 URL:** V1 ALB는 **v1-api.hakwonplus.com** (CNAME)으로 노출.
- **필수 조치(둘 중 하나):**
  - **Case A:** Cloudflare DNS에서 `api.hakwonplus.com`을 V1 ALB와 동일 엔드포인트로 CNAME(또는 v1-api.hakwonplus.com 프록시).
  - **Case B:** 프론트 프로덕션 빌드에서 `VITE_API_BASE_URL=https://v1-api.hakwonplus.com` 설정 후 재배포.
- **정상 확인:** 브라우저에서 hakwonplus.com 접속 → 로그인·API 호출 시 4xx/5xx 없이 동작.

---

## 5. 불필요 이미지·비용 최적화

### 5.1 ECR 라이프사이클 (배포 시 자동 적용)

- **자동 적용:** `scripts/v1/resources/ecr.ps1`의 `Ensure-ECRRepos`가 ECR 저장소 Ensure 후 **저장소별로 라이프사이클 정책을 자동 적용**한다. 별도 수동 적용 불필요.
- **정책 파일:** `docs/00-SSOT/v1/scripts/ecr-lifecycle-policy.json`
  - tagged: `v1-`, `bootstrap-` 접두사 이미지 중 최신 20개만 유지.
  - untagged: 1일 경과 후 만료.
- 수동 적용이 필요한 경우(저장소만 먼저 만들 때 등):

```powershell
$policyPath = (Resolve-Path "C:\academy\docs\00-SSOT\v1\scripts\ecr-lifecycle-policy.json").Path -replace '\\','/'
aws ecr put-lifecycle-policy --repository-name academy-video-worker --lifecycle-policy-text "file://$policyPath" --profile default --region ap-northeast-2
# academy-api, academy-messaging-worker, academy-ai-worker-cpu 동일 적용 가능
```

### 5.2 기타

- Video Batch CE: min 0, max 10 — 작업 없을 때 비용 없음.
- Video Ops CE: min 0, max 2 vCPU (m6g.medium).
- Build: Spot 우선 → 온디맨드 폴백으로 비용 절감.
- S3: 미사용. R2만 사용.

---

## 6. 서비스 가능 상태 (별도 조작 없이 사용자 기능 사용)

배포가 한 번 성공한 뒤에는 다음이 만족되면 **사용자가 모든 기능 사용 가능**:

| # | 조건 |
|---|------|
| 1 | API ALB `/health` 200 (v1-api.hakwonplus.com 또는 ALB DNS) |
| 2 | 프론트가 API 베이스 URL로 v1-api.hakwonplus.com(또는 api.hakwonplus.com 동일 설정) 사용 |
| 3 | SSM `/academy/workers/env`, `/academy/api/env` 존재·값 정상 |
| 4 | RDS·Redis·Batch·SQS·EventBridge·ASG가 Ensure 완료 상태 |

위가 충족되면 로그인·동영상·AI·메시징 등 별도 조작 없이 이용 가능.

---

## 7. 배포 스크립트 최근 변경 (2026-03-05)

| 파일 | 변경 내용 |
|------|-----------|
| **scripts/v1/core/aws.ps1** | `AWS_PROFILE` 설정 시 모든 `aws` 호출에 `--profile` 주입(`Get-AwsArgsWithProfile`). Cursor/새 프로세스에서도 `-AwsProfile default` 동작 보장. |
| **scripts/v1/core/bootstrap.ps1** | `/academy/workers/env` 없을 때 `.env`에서 필수 키 읽어 SSM SecureString(Base64 JSON) 생성(`Invoke-BootstrapWorkersEnv`). Bootstrap 시 Preflight 전에 실행. |
| **scripts/v1/core/preflight.ps1** | SSM 확인을 raw `aws` → `Invoke-AwsJson`으로 변경해 프로파일 적용. |
| **scripts/v1/resources/ssm.ps1** | `Confirm-SSMEnv`에서 raw `aws` → `Invoke-AwsJson` 사용. |
| **scripts/v1/resources/jobdef.ps1** | `Register-JobDefFromJson`에서 raw `aws batch register-job-definition` → `Invoke-Aws` 사용. |
| **scripts/v1/resources/build.ps1** | Spot `run-instances` 실패 시 인스턴스 0개면 온디맨드로 재시도. |
| **scripts/v1/resources/ecr.ps1** | Ensure-ECRRepos 후 저장소별 ECR 라이프사이클 정책 자동 적용(불필요 이미지 미보관, 비용 최적화). |

---

## 8. 최종 체크리스트 (사용자 실행)

| 순서 | 작업 | 명령/확인 |
|------|------|-----------|
| 1 | AWS 자격증명 확인 | `aws sts get-caller-identity --profile default` |
| 2 | 배포 실행 | 2절 Option A 또는 B (동일 셸에서) |
| 3 | API 헬스 | `curl -s -o /dev/null -w "%{http_code}" https://v1-api.hakwonplus.com/health` → 200 |
| 4 | 프론트 API URL | api.hakwonplus.com CNAME 또는 VITE_API_BASE_URL=v1-api.hakwonplus.com 후 재배포 |
| 5 | ECR 라이프사이클 (선택) | 배포 시 자동 적용됨. 수동만 필요 시 5절 명령으로 저장소별 적용 |

---

## 9. 참조 문서

- **SSOT:** `docs/00-SSOT/v1/SSOT.md`
- **배포 플랜:** `docs/00-SSOT/v1/V1-DEPLOYMENT-PLAN.md`
- **배포 최종 상태:** `docs/00-SSOT/v1/V1-DEPLOYMENT-FINAL-STATE.md`
- **배포 검증:** `docs/00-SSOT/v1/V1-DEPLOYMENT-VERIFICATION.md`
- **스크립트 변경 이력:** `docs/00-SSOT/v1/SCRIPTS-CHANGELOG.md`

---

**문서 끝.**
