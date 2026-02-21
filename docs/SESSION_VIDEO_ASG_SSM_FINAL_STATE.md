# 세션 정리 — Video ASG / SSM·API env 복구 최종 상태

**기준일**: 2026-02-22  
**목적**: 대화에서 진행된 작업의 **최종 상태만** 기록. 나중에 참고용.

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
