# 세션 정리 — Video ASG / SSM·API env 복구 최종 상태

**기준일**: 2026-02-22  
**목적**: 대화에서 진행된 작업의 **최종 상태만** 기록. 나중에 참고용.

---

## 0. 리소스 식별자 (추측 방지용)

아래는 이 프로젝트에서 쓰는 **실제 이름/ID**. 나중에 스크립트·문서 수정 시 여기 기준으로 사용. (인스턴스 재생성 등으로 바뀌면 `aws ec2 describe-instances` 등으로 재확인.)

| 구분 | 이름/값 | 비고 |
|------|---------|------|
| **Region** | `ap-northeast-2` | 서울 |
| **API EC2 태그** | `Name=academy-api` | describe-instances 필터용 |
| **API EC2 Private IP** | `172.30.3.142` | 인스턴스당 다를 수 있음. 바뀌면 Lambda ENV의 VIDEO_BACKLOG_API_INTERNAL URL도 수정 필요 |
| **API Docker 컨테이너** | `academy-api` | `docker exec academy-api ...`, `docker ps` |
| **API 이미지** | `academy-api:latest` | deploy 시 사용 |
| **EC2 API 호스트 경로** | `/home/ec2-user/.env`, `/home/ec2-user/academy` | SSH 후 env·프로젝트 루트 |
| **퍼블릭 API 도메인** | `api.hakwonplus.com` | Host 헤더로 넣으면 내부 호출에서 거절될 수 있음 → Lambda에는 넣지 말 것 |
| **VPC (API·Lambda 공통)** | `vpc-0831a2484f9b114c2` | HakwonPlus 메인 VPC |
| **Subnet (API·Lambda)** | `subnet-049e711f41fdff71b` | academy-api가 붙어 있는 서브넷 |
| **API 보안 그룹** | 이름 `academy-api-sg`, ID `sg-0051cc8f79c04b058` | API EC2 + Lambda→8000 허용할 SG |
| **Lambda 전용 SG** | 이름 `academy-lambda-endpoint-sg`, ID `sg-0944a30cabd0c022e` | Lambda에 붙이고, Endpoint/API SG 쪽에서 이 SG를 443/8000 허용 |
| **VPC Endpoint용 SG** | ID `sg-0ff11f1b511861447` | Monitoring/SQS Endpoint에 붙어 있음. Lambda SG(sg-0944...) → 443 허용 필요 |
| **Lambda 함수** | `academy-worker-queue-depth-metric` | queue depth → CloudWatch 메트릭 퍼블리시 |
| **SSM 파라미터** | `/academy/api/env`, `/academy/workers/env` | API용·워커용 env. **절대 한 줄로 덮어쓰지 말 것** |
| **Internal API 경로** | `/api/v1/internal/video/backlog-count/` | BacklogCount 조회. `X-Internal-Key` 필수 |
| **Video SQS 큐** | `academy-video-jobs` | |
| **Video Worker ASG** | `academy-video-worker-asg` | TargetTracking 정책: BacklogCount 기반 |
| **CloudWatch 메트릭** | Namespace `Academy/VideoProcessing`, MetricName `BacklogCount`, Dimensions `WorkerType=Video`, `AutoScalingGroupName=academy-video-worker-asg` | |
| **내부 API 키 (예시)** | `LAMBDA_INTERNAL_API_KEY=hakwonplus-internal-key` | 실제 값은 운영 비밀. API·Lambda 양쪽 동일해야 함 |

- **API Private IP**가 바뀌면: EC2 `describe-instances --filters Name=tag:Name,Values=academy-api` 로 새 PrivateIpAddress 확인 후 Lambda ENV `VIDEO_BACKLOG_API_INTERNAL` URL 수정.
- **SG/Subnet**은 가능하면 이 문서 값 사용하고, 인프라 변경 시에만 여기 적힌 ID를 갱신.

---

## 1. 완료된 작업 요약

| 구분 | 내용 |
|------|------|
| **verify_ssm_api_env.ps1** | SSM get을 Process + JSON 파싱으로 변경. 한국어 Windows cp949 회피를 위해 현재 프로세스에 `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1` 설정 후 자식(aws) 상속. True/False 출력 제거(`[void]` 등). AWS CLI 경로 자동 탐색(Get-Command + 고정 경로 후보). |
| **add_lambda_internal_key_api.ps1** | SSM get 실패/비어 있으면 **한 줄로 덮어쓰지 않고** 종료. "Refusing to overwrite SSM with a single key (would wipe DB_*, R2_*, etc.)" 메시지 출력. |
| **API env 복구 플로우** | SSM `/academy/api/env`에 전체 .env(DB_*, R2_*, REDIS_* 등) 유지. 로컬: `upload_env_to_ssm.ps1` → EC2: `deploy_api_on_server.sh` → `verify_api_after_deploy.sh`. DB null / 500 원인은 SSM 한 줄 덮어쓰기였음. |
| **Lambda (queue_depth_metric)** | 동일 VPC(vpc-0831...), API와 같은 subnet(subnet-049e711f41fdff71b). Lambda 전용 SG(sg-0944a30cabd0c022e) 사용. API SG(sg-0051cc8f79c04b058)에 Lambda SG → 8000 허용. Monitoring/SQS VPC Endpoint는 이미 존재. Endpoint SG(sg-0ff11f1b511861447)에 Lambda SG → 443 허용. |
| **Lambda ENV** | `VIDEO_BACKLOG_API_INTERNAL=http://172.30.3.142:8000/api/v1/internal/video/backlog-count/`, `LAMBDA_INTERNAL_API_KEY=hakwonplus-internal-key` 설정됨. |

---

## 2. 현재 동작 상태

- **로컬**: `.\scripts\verify_ssm_api_env.ps1` → SSM에 DB_*, REDIS_*, R2_* 있으면 OK. 없으면 `upload_env_to_ssm.ps1` 안내.
- **EC2 API**: `deploy_api_on_server.sh` 후 `settings.DATABASES` HOST/NAME 채워짐. 컨테이너 내부에서 `GET /api/v1/internal/video/backlog-count/` + `X-Internal-Key` → **200** 확인됨.
- **Lambda invoke**: timeout 없이 종료. 응답 예: `{"ai_queue_depth":0,"video_queue_depth":5,"video_backlog_count":null,"messaging_queue_depth":0}`.
- **video_backlog_count: null** 인 이유: Lambda가 `Host: api.hakwonplus.com`(VIDEO_BACKLOG_API_HOST)으로 172.30.3.142에 요청해 Django ALLOWED_HOST 등에서 거절될 수 있음.

---

## 3. 자동 스케일까지 하려면 (한 가지)

- Lambda에서 **VIDEO_BACKLOG_API_HOST 제거** (설정에서 빼기).
- 그 후 `aws lambda invoke ...` 시 `video_backlog_count`에 숫자(예: 5)가 오면, BacklogCount 메트릭이 CloudWatch에 퍼블리시되고 academy-video-worker-asg TargetTracking이 동작해 **워커가 자동으로 늘어남**.

```powershell
aws lambda update-function-configuration `
  --function-name academy-worker-queue-depth-metric `
  --region ap-northeast-2 `
  --environment "Variables={VIDEO_BACKLOG_API_INTERNAL=http://172.30.3.142:8000/api/v1/internal/video/backlog-count/,LAMBDA_INTERNAL_API_KEY=hakwonplus-internal-key}"
```

(위 예시에는 VIDEO_BACKLOG_API_HOST를 넣지 않음.)

---

## 4. 참고 문서

| 문서 | 설명 |
|------|------|
| docs/VERIFY_RECOVERY_SCRIPTS_RESULT.md | verify_ssm_api_env.ps1, deploy_api_on_server.sh, verify_api_after_deploy.sh 검증 결과 |
| docs/API_ENV_DEPLOY_FLOW.md | API env 배포 플로우 (SSOT = SSM) |
| docs/API_ENV_RECOVERY_STRICT.md | API env 복구 절차 (strict) |
| docs/VIDEO_ENTERPRISE_JOB_MIGRATION_FINAL_REPORT.md | Video Job 기반 파이프라인 |
| docs/B1_IMPLEMENTATION_FINAL_REPORT.md | B1 BacklogCount TargetTracking |

---

## 5. 한 줄 요약

- **SSM/API env**: 복구 플로우 정리됨. `verify_ssm_api_env.ps1` 정상 동작. SSM 한 줄 덮어쓰기 방지 적용.
- **Lambda**: 같은 VPC·Endpoint·SG 적용으로 timeout 해소. SQS/CloudWatch 퍼블리시 정상.
- **Video ASG 자동 스케일**: Lambda에서 `VIDEO_BACKLOG_API_HOST` 제거 후 backlog API가 200+숫자 반환하면 BacklogCount 메트릭 → TargetTracking으로 워커 자동 증가.
