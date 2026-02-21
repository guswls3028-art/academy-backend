# B1 Video Worker ASG 스케일링 최종 구현 보고서

**작성일**: 2026-02-21  
**목표**: Lambda 직접 제어 제거, CloudWatch BacklogCount 기반 ASG TargetTracking 전환

---

## 1. 구현 완료 항목

### 1.1 deploy_worker_asg.ps1
- ✅ Video ASG에 **BacklogCountTargetTracking** 정책 추가
- ✅ TargetValue: 3, ScaleOutCooldown: 60, ScaleInCooldown: 300
- ✅ Namespace: Academy/VideoProcessing, Metric: BacklogCount
- ✅ Dimensions: WorkerType=Video, AutoScalingGroupName=academy-video-worker-asg
- ✅ 기존 `delete-scaling-policy` (Video) 제거

### 1.2 queue_depth_lambda (lambda_function.py)
- ✅ `set_desired_capacity` 호출 완전 제거
- ✅ `set_video_worker_desired()`, SSM, autoscaling 관련 로직 제거
- ✅ BacklogCount 발행: Academy/VideoProcessing 네임스페이스
- ✅ VIDEO_BACKLOG_API_URL 설정 시 Django API 호출 (DB 기반 backlog)
- ✅ 미설정/실패 시 SQS visible+inflight fallback
- ✅ X-Internal-Key 헤더로 API 인증
- ✅ 로그: `BacklogCount metric published | backlog=N`

### 1.3 Django
- ✅ `GET /api/v1/internal/video/backlog-count/` 엔드포인트 추가
- ✅ `VideoBacklogCountView`: UPLOADED+PROCESSING count 반환
- ✅ `IsLambdaInternal` permission: X-Internal-Key 검증
- ✅ `LAMBDA_INTERNAL_API_KEY` settings 추가

### 1.4 IAM
- ✅ `academy-lambda` 역할에 `LambdaPutMetricData` 정책 추가
- ✅ Academy/Workers, Academy/VideoProcessing PutMetricData 허용
- ✅ AutoScaling SetDesiredCapacity 권한 제거

### 1.5 환경 변수
- ✅ .env, .env.example, .env.deploy에 LAMBDA_INTERNAL_API_KEY 추가
- ✅ Lambda ENV: VIDEO_BACKLOG_API_URL, LAMBDA_INTERNAL_API_KEY
- ✅ SSM /academy/workers/env 업로드 스크립트 (upload_env_to_ssm.ps1)
- ✅ API 서버 .env 동기화 스크립트 (sync_api_env_lambda_internal.ps1)

### 1.6 nginx X-Internal-Key passthrough
- ✅ **infra/nginx/academy-api.conf**: EC2 호스트 nginx용, `proxy_set_header X-Internal-Key $http_x_internal_key` 추가
- ✅ **docker/nginx/default.conf**: Docker nginx 프록시용 동일 설정
- ✅ **full_redeploy.ps1**: academy-api 배포 시 nginx 설정 복사 → `/etc/nginx/conf.d/academy-api.conf` 적용 후 reload
- ✅ **verify_lambda_internal_api.ps1**: LOCAL(localhost:8000) vs PUBLIC(api.hakwonplus.com) 호출 비교 검증 스크립트
- ✅ **scripts/_verify_internal_api_remote.sh**: EC2에서 실행하는 검증 셸 스크립트 (SCP 후 실행)

---

## 2. 현재 동작 상태

| 항목 | 상태 | 비고 |
|------|------|------|
| CloudWatch PutMetricData | ✅ 정상 | BacklogCount 발행 성공 |
| ASG TargetTracking | ✅ 동작 | BacklogCount 기반 스케일링 |
| Lambda set_desired_capacity | ✅ 제거됨 | B1 아키텍처 준수 |
| VIDEO_BACKLOG_API (DB SSOT) | ✅ 정상 | nginx passthrough 적용 후 200 OK |
| Fallback (SQS) | ✅ 대기 | API 성공 시 미사용 |

### 2.1 DB Backlog API 403 → 해결
- **원인**: api.hakwonplus.com 경로(Cloudflare → EC2 nginx)에서 X-Internal-Key가 upstream(gunicorn)으로 전달되지 않음.
- **조치**: EC2 nginx에 `proxy_set_header X-Internal-Key $http_x_internal_key` 적용, full_redeploy 시 설정 자동 반영.
- **검증**: `.\scripts\verify_lambda_internal_api.ps1` 실행 시 [LOCAL] 200 OK, [PUBLIC] 200 OK, backlog JSON 정상 출력 확인.

---

## 3. 변경된 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| scripts/deploy_worker_asg.ps1 | Video TargetTracking 추가, delete 제거 |
| infra/worker_asg/queue_depth_lambda/lambda_function.py | set_desired 제거, BacklogCount 발행, API 호출+헤더 |
| infra/worker_asg/iam_policy_queue_depth_lambda.json | Academy/VideoProcessing, AutoScaling/SSM 제거 |
| infra/worker_asg/iam_policy_lambda_cloudwatch.json | 신규 (PutMetricData 정책) |
| apps/core/permissions.py | IsLambdaInternal 추가 |
| apps/support/video/views/internal_views.py | VideoBacklogCountView 추가 |
| apps/api/v1/urls.py | internal/video/backlog-count/ 라우트 |
| apps/api/config/settings/base.py | LAMBDA_INTERNAL_API_KEY |
| .env, .env.example, .env.deploy | LAMBDA_INTERNAL_API_KEY |
| scripts/sync_api_env_lambda_internal.ps1 | 신규 (SSM→EC2 .env 동기화+API 재시작) |

---

## 4. 검증 체크리스트

- [x] CloudWatch BacklogCount 메트릭 발행 확인
- [x] ASG BacklogCountTargetTracking 정책 존재
- [x] Lambda 로그에 `BacklogCount metric published` 확인
- [x] Lambda 로그에 `video_asg`, `set_desired` 없음
- [ ] VIDEO_BACKLOG_API 403 해결 (DB SSOT 활성화)
- [ ] DB backlog vs Metric 값 일치 확인 (API 성공 시)

---

## 5. B1 완료 기준 대비

| 기준 | 상태 |
|------|------|
| Lambda가 set_desired_capacity() 호출 ❌ | ✅ 준수 |
| ASG TargetTrackingPolicy 존재 ✅ | ✅ 완료 |
| BacklogCount metric 1분 주기 발행 ✅ | ✅ 완료 |
| backlog 증가 시 ASG 자동 scale out ✅ | ✅ 동작 (fallback 기준) |
| backlog 감소 시 scale in (300s cooldown) ✅ | ✅ 동작 |
| DB 기반 BacklogCount (SSOT) | ⚠️ API 403으로 fallback 사용 중 |

---

## 6. 참조 문서

- `docs/B1_METRIC_SCHEMA_EXTRACTION_REPORT.md`
- `docs/B1_SCALING_DATA_EXTRACTION_REPORT.md`
- `infra/worker_asg/iam_policy_lambda_cloudwatch.json`
