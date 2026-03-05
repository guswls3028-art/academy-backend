# V1 배포 검증 리포트

**AI·Cursor 룰:** 본 문서를 포함한 리포지토리 내 **모든 문서·코드에 대해 AI(Cursor Agent)는 열람·수정 권한**이 있다. 검증·배포·인프라 작업 시 **.cursor/rules/** 내 해당 룰을 **적재적소에 항시 확인**한다.

**검증 일시:** 2026-03-05  
**기준:** 제공된 입력 정보 + 실제 AWS CLI / Cloudflare API·wrangler 호출 결과만 사용. 추측 없음.

---

## 1. 검증 목적 및 방법

- **목적:** Phase 0~3 완료 후 AWS·Cloudflare 상태가 V1 배포 기준에 맞는지 확인.
- **방법:** `aws` (--profile default, ap-northeast-2), `curl`, `npx wrangler`, Cloudflare API 호출 결과만 기반으로 기술.

---

## §2 AWS 배포 검증

### 2.1 ALB `/health` 및 Target Group

| 항목 | 명령/방법 | 결과 (실제 값) |
|------|-----------|----------------|
| ALB DNS | 고정 | `academy-v1-api-alb-1317506512.ap-northeast-2.elb.amazonaws.com` |
| `/health` 호출 | `curl -s -w "\nHTTP_CODE:%{http_code}" --connect-timeout 15 "http://.../health"` | **HTTP_CODE:000** (연결 타임아웃, exit 28) |
| Target Group ARN | `aws elbv2 describe-target-groups --names academy-v1-api-tg` | `arn:aws:elasticloadbalancing:ap-northeast-2:809466760795:targetgroup/academy-v1-api-tg/bb2c965190169007` |
| Target Health | `aws elbv2 describe-target-health --target-group-arn <ARN>` | **unhealthy** — Target Id: `i-0c1d35ece5179aa6e`, Reason: **Target.Timeout**, Description: "Request timed out" |

**요약:** ALB 자체는 active이나, 타깃 1대(i-0c1d35ece5179aa6e)가 **unhealthy (Target.Timeout)**. 로컬에서 ALB `/health` curl 시 응답 없음(000).

### 2.2 원인(실제 조회 결과)

| 항목 | 명령/방법 | 결과 (실제 값) |
|------|-----------|----------------|
| API 인스턴스 보안 그룹 | `aws ec2 describe-instances --instance-ids i-0c1d35ece5179aa6e` | **sg-011ed1d9eb4a65b8f** → **academy-video-batch-sg** |
| V1 API용 보안 그룹 | `aws ec2 describe-security-groups --filters Name=group-name,Values=academy-v1-sg-app` | **sg-088fa3315c12754d0** (academy-v1-sg-app) |
| API Launch Template SG | `aws ec2 describe-launch-template-versions --launch-template-name academy-v1-api-lt --versions 2` | **SecurityGroupIds: ["sg-011ed1d9eb4a65b8f"]** (Batch SG) |

**결론:** API 인스턴스에 **academy-video-batch-sg**가 붙어 있음. academy-v1-sg-app은 8000 포트 인바운드 등이 있으나, Batch SG에는 ALB→8000 허용이 없거나 다름. **Launch Template에 잘못된 SG가 지정된 상태였음.**

### 2.3 조치 적용 (검증 시점에 실행한 내용)

- **params.yaml:** `network.securityGroupApp`를 `"sg-088fa3315c12754d0"`으로 명시.
- **Launch Template:** academy-v1-api-lt 버전 4 생성 — SecurityGroupIds `["sg-088fa3315c12754d0"]`, 나머지 데이터(AMI, 인스턴스 타입, IAM 프로필, 태그) 유지.
- **LT 기본 버전:** 4로 설정.
- **인스턴스 리프레시:** academy-v1-api-asg에 start-instance-refresh 실행 (InstanceRefreshId: e7a60fc5-7b4f-4767-83fb-8e6e7e9d03ae).

리프레시 완료 후 새 인스턴스는 academy-v1-sg-app(sg-088fa3315c12754d0)으로 기동되며, Target Health가 healthy로 전환되는지 재검증 필요.

### 2.4 생성된 리소스 목록 (실제 조회)

| 유형 | 명령 | 결과 (존재·상태만) |
|------|------|---------------------|
| RDS | `describe-db-instances --db-instance-identifier academy-db` | **academy-db** — available, Endpoint: academy-db.cbm4oqigwl80.ap-northeast-2.rds.amazonaws.com |
| Redis | `describe-replication-groups --replication-group-id academy-v1-redis` | **available**, Primary: academy-v1-redis.prqwaq.ng.0001.apn2.cache.amazonaws.com |
| DynamoDB | `describe-table --table-name academy-v1-video-job-lock` | **academy-v1-video-job-lock** — ACTIVE |
| Batch CE | `describe-compute-environments` (academy-v1-*) | academy-v1-video-batch-ce: ENABLED, VALID / academy-v1-video-ops-ce: ENABLED, VALID |
| Batch Queue | `describe-job-queues` (academy-v1-*) | academy-v1-video-batch-queue, academy-v1-video-ops-queue: ENABLED, VALID |
| EventBridge | `list-rules --name-prefix academy-v1` | academy-v1-reconcile-video-jobs, academy-v1-video-scan-stuck-rate |
| ALB | `describe-load-balancers --names academy-v1-api-alb` | academy-v1-api-alb — State: **active**, DNSName 확인됨 |
| SQS | `list-queues --queue-name-prefix academy-v1` | academy-v1-ai-queue, academy-v1-messaging-queue |
| ECR | `describe-repositories` | academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu, academy-base |
| ASG | `describe-auto-scaling-groups` (academy-v1-*) | academy-v1-api-asg (1,1,2), academy-v1-ai-worker-asg (1,1,10), academy-v1-messaging-worker-asg (1,1,10), academy-v1-video-ops-ce-asg-* (Batch 관리) |

### 2.5 S3 사용 금지 확인

| 항목 | 명령 | 결과 |
|------|------|------|
| AWS S3 버킷 | `aws s3 ls --profile default` | 출력 없음 → **버킷 0개** (S3 미사용) |
| 코드베이스 | libs/s3_client/client.py | endpoint_url=settings.R2_ENDPOINT, R2_* 사용 → **R2(S3 호환)만 사용, AWS S3 아님** |

### 2.6 SSM Agent 연결 상태

| 항목 | 명령 | 결과 (실제 값) |
|------|------|----------------|
| SSM 등록 인스턴스 | `aws ssm describe-instance-information` | **i-0bcc8ceba665d38eb** — PingStatus: **Online** (1대만 등록) |
| API ASG 인스턴스 | `describe-instances` (i-0c1d35ece5179aa6e, i-0bcc8ceba665d38eb) | i-0c1d35ece5179aa6e: Name=**academy-v1-api**, running / i-0bcc8ceba665d38eb: Name=**None**, running |

**결론:** 현재 API 인스턴스(**i-0c1d35ece5179aa6e**)는 **SSM Instance Information 목록에 없음**. SSM Online인 i-0bcc8ceba665d38eb는 Name 태그 없음(다른 역할 가능성). 따라서 **API 인스턴스 SSM Agent 미연결** 상태로 기록.

### 2.7 배포 스크립트·완료 여부

- Phase 0~2 실행으로 리소스는 생성됨.
- API ASG 생성 후 "SSM agent 대기" 단계에서 타임아웃 발생한 것은 제공된 입력과 일치.
- **발견된 문제:** SSOT 로드 시 `network.securityGroupApp`이 비어 있으면 `ApiSecurityGroupId`가 `BatchSecurityGroupId`로 폴백(scripts/v1/core/ssot.ps1 100~101행). params에 `securityGroupApp` 미입력 시 API LT에 Batch SG가 들어감. → **params에 securityGroupApp 명시** 및 **LT 수정**으로 위에서 조치함.

---

## §3 Cloudflare 검증

### 3.1 R2 버킷

| 항목 | 명령 | 결과 (실제 값) |
|------|------|----------------|
| 버킷 목록 | `npx wrangler r2 bucket list` (프로젝트 루트, .env 로드) | **5개:** academy-admin, academy-ai, academy-excel, academy-storage, academy-video (이름·creation_date 확인) |

### 3.2 CDN(Zone) 목록 및 상태

| 항목 | 명령 | 결과 (실제 값) |
|------|------|----------------|
| Zone 목록 | Cloudflare API GET /zones (X-Auth-Email, X-Auth-Key from .env) | **4개 Zone**, status 모두 **active**: hakwonplus.com, limglish.kr, tchul.com, ymath.co.kr |

### 3.3 인증 정보(.env) 적용 여부

| 항목 | 방법 | 결과 |
|------|------|------|
| R2 접근 | `npx wrangler r2 bucket list` | 성공 → **CLOUDFLARE_ACCOUNT_ID, API 키 등 .env 적용 정상** |
| Zone 조회 | Cloudflare API + .env의 CLOUDFLARE_EMAIL, CLOUFDLARE_API_KEY | 성공 → **Zone 접근 가능** |

---

## 4. 문제 발견 시 조치 체크리스트

| # | 항목 | 상태 | 조치 |
|---|------|------|------|
| 1 | ALB `/health` 200 미확인 | **실패** | Target healthy 전환 후 재확인. (조치: LT SG 수정·인스턴스 리프레시 완료) |
| 2 | Target Group healthy | **실패** (unhealthy, Target.Timeout) | LT를 academy-v1-sg-app으로 수정 후 인스턴스 리프레시 실행함. 리프레시 완료 후 describe-target-health로 재확인. |
| 3 | API 인스턴스 보안 그룹 오설정 | **조치함** | params.yaml에 securityGroupApp 명시, LT 버전 4(sg-app) 생성·기본값 설정, 인스턴스 리프레시 실행. |
| 4 | API 인스턴스 SSM Agent 미연결 | **미해결** | i-0c1d35ece5179aa6e SSM 미등록. 리프레시 후 새 인스턴스 SSM 등록 여부 확인 필요. 필요 시 인스턴스 역할(SSM 정책)·VPC 엔드포인트·네트워크 경로 점검. |
| 5 | S3 사용 금지 | **준수** | S3 버킷 0개, 코드는 R2 엔드포인트만 사용. |

---

## 5. 최종 판단

- **배포 상태를 "완료"로 안전하게 인정 가능한가?**  
  **아니오.**  
  - ALB `/health`가 200으로 확인되지 않았고, Target이 unhealthy(Target.Timeout)이며, 원인인 API LT 보안 그룹 오설정에 대한 수정(리프레시)은 적용했으나 **리프레시 완료 및 Target healthy·/health 200 확인이 아직 남아 있음.**  
  - SSM Agent는 현재 API 인스턴스 기준 미연결 상태이며, 리프레시 후 새 인스턴스에 대해 한 번 더 확인하는 것이 좋음.

- **안전하게 "완료"로 보려면:**  
  1) 인스턴스 리프레시 완료 대기 후,  
  2) `aws elbv2 describe-target-health`로 타깃 **healthy** 확인,  
  3) `curl http://academy-v1-api-alb-1317506512.ap-northeast-2.elb.amazonaws.com/health`로 **200** 확인,  
  4) (선택) 새 API 인스턴스에 대해 `aws ssm describe-instance-information`으로 SSM Online 확인.

이후 위 2~3이 만족되면 배포 상태 "완료"로 보는 것이 타당함.

---

## 6. 배포 스크립트 변경에 따른 검증 참고 (2026-03-05)

- **자격증명:** `deploy.ps1 -AwsProfile default` 사용 시 `core/aws.ps1`에서 모든 `aws` 호출에 `--profile`이 주입됨. 동일 셸에서 `aws sts get-caller-identity --profile default` 성공한 뒤 `deploy.ps1` 실행하면 토큰 오류 없이 동작.
- **SSM workers env:** Bootstrap에서 `/academy/workers/env`가 없으면 `.env`에서 읽어 생성. Preflight SSM 확인은 `Invoke-AwsJson` 사용으로 프로파일 적용됨.

---

## 7. 3시간 영상 검증 절차 (V1 보강 후)

V1이 standard/long 2-tier·timeout·stuck(heartbeat_age)·R2 checkpoint·관측 SSOT로 보강된 상태에서, 배포 후 아래 절차로 정상 케이스를 검증한다.

### 7.1 3시간 샘플 1건 완주

| 단계 | 작업 | 확인 |
|------|------|------|
| 1 | 3시간 분량 원본 업로드 (duration ≥ 10800초로 long 큐 사용되는지 확인) | API/DB에서 Video 생성 후 Batch 제출 시 `VIDEO_LONG_DURATION_THRESHOLD_SECONDS`(10800) 이상이면 long 큐/JobDef 사용 |
| 2 | Batch Job 제출 | `academy-v1-video-batch-long-queue`, `academy-v1-video-batch-long-jobdef` 사용 여부 로그 확인 |
| 3 | Job RUNNING → heartbeat 유지 | `last_heartbeat_at` 주기 갱신 (VIDEO_JOB_HEARTBEAT_SECONDS) |
| 4 | Job 완료 대기 (최대 12h timeout) | Job SUCCEEDED, Video.status READY, HLS 재생 가능 |
| 5 | Stuck 미판정 | heartbeat_age 45분 미만 유지 시 scan_stuck에서 RETRY_WAIT/DEAD 처리 없음 |

### 7.2 동시 3건

| 단계 | 작업 | 확인 |
|------|------|------|
| 1 | 동시에 3건 업로드·Batch 제출 (standard 또는 long 혼합 가능) | 3개 Job이 각각 QUEUED → RUNNING, 서로 간섭 없이 처리 |
| 2 | Queue depth | standard/long 큐별 대기 건수 확인 (observability.queueDepthAlarmThreshold 50 초과 시 알람) |
| 3 | 완료 | 3건 모두 SUCCEEDED 및 HLS 정상 |

### 7.3 업로드 실패 복구

| 단계 | 작업 | 확인 |
|------|------|------|
| 1 | R2 multipart 업로드 중 네트워크 단절 시뮬레이션 (또는 part 실패) | DynamoDB checkpoint table(academy-v1-video-upload-checkpoints)에 진행 상황 저장 |
| 2 | 재시도 | part_size 64MB, max_concurrency 8, max_attempts 8 기준으로 재개·완료 |
| 3 | 실패 시 | job_fail_retry → 네트워크/업로드 원인만 재시도, ffmpeg/콘텐츠 오류는 재시도 최소화(retryFfmpegContentAttempts 1) |

### 7.4 디스크 피크 측정

| 단계 | 작업 | 확인 |
|------|------|------|
| 1 | Batch 인스턴스 루트 볼륨 | standard CE 200GB(gp3), long CE 300GB(gp3) 권장(params rootVolumeSizeGb). Launch Template으로 CE 생성 시 반영 |
| 2 | 워커 임시 디스크 | process_video 내 temp_workdir 사용 후 정리; 세그먼트 생성 즉시 R2 업로드 후 로컬 삭제로 디스크 피크 완화 |
| 3 | (선택) CloudWatch 디스크 메트릭 | Batch 인스턴스에서 disk_used_percent 등 커스텀 메트릭 수집 시 피크 값 기록 |

### 7.5 관측·알람

| 항목 | SSOT (params.yaml) | 적용 |
|------|---------------------|------|
| Queue depth 알람 | videoBatch.observability.queueDepthAlarmThreshold: 50 | CloudWatch Alarm에서 해당 큐 depth ≥ 50 시 알람 |
| Failed jobs 알람 | videoBatch.observability.failedJobsAlarmThreshold: 5 | Batch failed job 수 ≥ 5 시 알람 |
| Stuck detected | videoBatch.observability.stuckDetectedAlarmEnabled: true | scan_stuck에서 DEAD/RETRY_WAIT 전환 시 이벤트 발송 시 알람 연동 |
| 로그 보존 | videoBatch.observability.logRetentionDays: 30 | `/aws/batch/academy-video-worker` 등 로그 그룹에 put-retention-policy 30일 적용 |

---

**문서 끝.**
