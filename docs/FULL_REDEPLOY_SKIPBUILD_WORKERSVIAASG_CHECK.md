# full_redeploy.ps1 -SkipBuild -WorkersViaASG 검사 결과

**실행 예시:**  
`.\scripts\full_redeploy.ps1 -GitRepoUrl "..." -SkipBuild -WorkersViaASG`

**기본 전제:** Video EC2/ASG는 이 스크립트 배포 대상에 포함되지 않음.  
이 옵션 조합에서 정책·환경변수·Lambda·IAM 등에 영향이 없는지 검사함.

---

## 1. 실행 흐름 요약 (DeployTarget=all 기본값)

| 단계 | -SkipBuild | -WorkersViaASG | 동작 |
|------|------------|----------------|------|
| 1) Build | 생략 | - | 빌드 인스턴스 기동/SSM/ECR 푸시 없음. `-GitRepoUrl` 불필요. |
| 2) API 배포 | - | - | **실행됨.** academy-api EC2에 SSH → .env 복사, docker pull/run, Batch 설정 검증, nginx 설정 복사 |
| 3) Worker 배포 | - | 적용 | **ASG Instance Refresh만.** academy-ai-worker-asg, academy-messaging-worker-asg 에 대해 `start-instance-refresh` 호출. SSH 배포 없음. |

- Video 워커: `$workerList`에 포함되지 않음. `$asgMap`에도 video ASG 없음. **영향 없음.**

---

## 2. 정책·환경변수·설정 영향

### 2.1 변경되는 것 (의도된 동작)

| 대상 | 내용 | Video/정책 영향 |
|------|------|------------------|
| **API 서버 .env** | 로컬(리포)의 `.env`를 `scp`로 API EC2 `/home/ec2-user/.env`에 덮어씀 | API 컨테이너가 사용. `VIDEO_BATCH_JOB_QUEUE`, `VIDEO_BATCH_JOB_DEFINITION` 등이 여기 있으면 API가 Batch 제출 시 사용. **정상 동작.** |
| **API 컨테이너** | ECR에서 `academy-api:latest` pull 후 `--env-file .env`로 재기동 | 위 .env 적용. 정책/환경변수 추가·삭제는 스크립트가 하지 않음. |
| **API 서버 nginx** | `infra\nginx\academy-api.conf` 복사 후 reload | `X-Internal-Key` 헤더 전달만 설정. Lambda backlog-count 인증용. **Video/Batch 정책과 무관.** |
| **ASG** | academy-ai-worker-asg, academy-messaging-worker-asg 에 대해 **Instance Refresh** | 새 인스턴스는 기존 Launch Template/User Data/SSM(`/academy/workers/env`) 기준으로 기동. **이 스크립트는 SSM·Launch Template을 수정하지 않음.** |

### 2.2 변경하지 않는 것 (영향 없음)

| 항목 | 확인 |
|------|------|
| **Lambda** | 호출 없음. `queue_depth_lambda`, `worker_autoscale_lambda` 등 환경변수/정책 변경 없음. |
| **IAM** | 호출 없음. 역할/정책 추가·수정 없음. |
| **SSM** | 호출 없음. `/academy/workers/env` 등 파라미터 변경 없음. |
| **Batch** | 호출 없음. Job Queue, Job Definition, CE, IAM 역할 등 변경 없음. |
| **Video ASG/EC2** | `$workerList`·`$asgMap`·`Get-Ec2PublicIps` 필터에 video 없음. 기동/배포 대상 아님. |

### 2.3 검증 스크립트 (읽기 전용)

- **check_api_batch_runtime.ps1**  
  API 컨테이너 안에서 `python manage.py check_batch_settings` 실행만 함.  
  설정 **조회만** 하며, env/정책/파일을 **수정하지 않음**. 실패 시 배포 중단.

---

## 3. 주의사항

1. **API 서버가 이미 실행 중이어야 함**  
   `-WorkersViaASG` 사용 시 `Start-StoppedAcademyInstances`를 호출하지 않음.  
   academy-api가 중지 상태면 Public IP가 없어 `Get-Ec2PublicIps`에 안 나오고,  
   "academy-api not found or has no public IP" 로 실패함.  
   → 필요 시 수동으로 API 인스턴스 기동 후 재실행.

2. **.env 내용**  
   API에 복사되는 .env는 **리포의 `.env`** 한 종류뿐.  
   Video Batch용 `VIDEO_BATCH_*` 등은 이 파일에 두면 API 배포 시 함께 반영됨.  
   (별도 정책/역할은 이 스크립트가 건드리지 않음.)

3. **Worker 환경변수**  
   Worker는 ASG Instance Refresh로만 갱신되므로,  
   인스턴스 기동 시 사용하는 env는 **Launch Template / User Data / SSM** 기준.  
   이 스크립트는 SSM 등을 수정하지 않으므로, Worker용 env 변경은 별도 절차 필요.

---

## 4. 결론

| 질문 | 답 |
|------|----|
| Video EC2/ASG 제외 여부 | ✅ 제외됨. worker 목록·ASG 맵·EC2 필터에 video 없음. |
| Lambda 정책/환경변수 변경 여부 | ✅ 변경 없음. Lambda 호출 없음. |
| IAM/SSM/Batch 리소스 변경 여부 | ✅ 변경 없음. |
| API 서버 .env/nginx | ✅ API 서버만 로컬 .env·nginx 설정으로 갱신. Video Batch에는 유리 (API가 올바른 .env로 Batch 제출). |

**정리:**  
`.\scripts\full_redeploy.ps1 -GitRepoUrl "..." -SkipBuild -WorkersViaASG` 는  
**전체 배포(API 배포 + AI/Messaging 워커 ASG만 갱신)** 이며,  
정책·환경변수·Lambda·IAM에는 영향을 주지 않고, Video EC2/ASG는 배포 대상에 포함되지 않음.
