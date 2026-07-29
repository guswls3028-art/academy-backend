# 장애 대응 런북

**Version:** V1.11.28 | **최종 수정:** 2026-07-28

> 모든 AWS 명령은 `scripts/v1/run-with-env.ps1 --` 접두사를 사용한다.
> 아래에서 `RUN_ENV`는 이 접두사의 줄임말이다:
> ```
> powershell -File scripts/v1/run-with-env.ps1 --
> ```

---

## 0. 사용자 오류 운영자 문자

`Dev Alerts Cron`이 사용자 오류 신호를 5분마다, 기존 결제·worker 등 전체 운영 룰을
매시 2분에 평가한다.

- API의 사용자 경로에서 반환된 HTTP 5xx
- 브라우저의 지속적인 React/window/unhandled-rejection 오류
- 공용 `문제 신고` 모달 접수
- 관리자·선생님 개발자 메뉴의 `[BUG]` 제보

늦게 확정 실패한 공급사 attempt도 놓치지 않도록 매번 2일 보존 범위 전체를 읽는다.
같은 테넌트·경로·오류 유형은 15분 단위로 묶고, 성공했거나 공급사 결과가 미확정인
발송 fingerprint는
`OpsAuditLog(action=alerts.user_incident_sms)`에 남겨 다시 보내지 않는다. 문자에는
서버가 확인한 플랫폼 발급 테넌트 코드·내부 ID와 통제된 사유(`서버5xx`, `화면오류`, `직접신고`,
`버그제보`), 유형별 건수, `/dev` 안내만 포함한다. 사용자 입력 본문·경로·학생명·
전화번호·예외명/메시지와 owner가 수정할 수 있는 테넌트명은 넣지 않는다. 테넌트
코드는 소문자 ASCII allowlist를 통과한 경우에만 내부 ID와 함께 표시하고, 아니면
`T{id}`로 대체하며 전체 식별자는 24 UTF-8 byte로 제한한다.

본문은 한 cron 실행당 SMS 1건으로 집계한다. 90 byte 안에 들어오는 테넌트를
발생 건수 순으로 표시하며, 나머지는 `+N곳`으로 표시한다. `+N곳`의 상세 테넌트와
경로는 `/dev` 감사 로그에서 확인한다. 성공 receipt에는 전체 fingerprint를 기록하므로
표시 공간 때문에 생략된 테넌트도 중복 발송하지 않는다.

```text
[학원+] 오류3건/2곳
알파#12:서버500(2) 베타#19:화면오류(1)
/dev
```

5xx 폭주가 장애 중 DB 부하를 증폭하지 않도록 같은 테넌트·경로·오류 유형은 API
프로세스별 60초에 1건만 bounded 비동기 큐로 감사 로그에 저장한다. 사용자 응답은
DB INSERT를 기다리지 않으며 PII 없는 동일 신호를 애플리케이션 로그에도 남긴다.
문자 건수는 이 샘플 수이며 원시 요청 횟수는 CloudWatch/Sentry에서 확인한다.

DB 장애처럼 감사 로그 자체를 쓸 수 없는 상황은 `academy-api-Target5XX`와
`HealthyHostCount < 1`인 `academy-api-UnHealthyHosts`를 묶은
`academy-api-UserImpact` composite alarm으로 독립 감지한다. 배포 중 새 대상이
준비되는 동안 기존 정상 대상이 하나라도 있으면 사용자 영향 장애로 보지 않는다.
이 외부 신호 문자는 `5xx 급증 또는 정상 서버 0대`로 두 원인을 구분해 표시한다.
cron은 alarm transition timestamp를 SSM에 발송 전 `claimed:` 상태로 기록하고,
Solapi `sent_success` 확인 뒤 `delivered:` 상태로 바꾼다. 원격 명령 결과가
유실되거나 timeout이어도 같은 transition을 자동 재발송하지 않는다.

운영자 SMS는 고객 알림톡/SMS 경로와 분리되어 있다. 수신번호와 발신번호 모두 코드와
환경설정 양쪽에서 `01031217466`으로 고정하며 다른 번호는 provider 호출 전에 차단한다.
Solapi 문자 발신번호도 운영 계정의 유일한 ACTIVE 등록 번호인 같은 통제번호로
정규화한다. 미등록 발신번호는 Solapi 상태코드 `1062`로 접수 거절되므로 설정
스크립트가 수신번호와 발신번호를 함께 갱신한다.

```powershell
# 설정 활성화 (cron이 매 실행 전 SSM에서 runtime env를 원자적으로 동기화)
pwsh scripts/v1/set-dev-alerts-sms.ps1 -AwsProfile default

# SSM env를 원자적으로 동기화한 pinned image로 통제번호에 1건 발송.
# 본문에는 owner 테넌트 식별값과 통제 사유 `서버500`이 테스트 표시와 함께 나온다.
gh workflow run dev-alerts-cron.yml -f test_sms=true
Start-Sleep -Seconds 3
$runId = gh run list --workflow dev-alerts-cron.yml --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $runId --exit-status

# 발송 없이 사용자 오류 룰만 평가. 전체 운영 룰까지 보려면 full_rules=true 추가.
gh workflow run dev-alerts-cron.yml -f dry_run=true

# 비상 비활성화
pwsh scripts/v1/set-dev-alerts-sms.ps1 -Disable -AwsProfile default
```

활성화/비활성화는 다음 5분 cron 실행부터 반영되며 API 컨테이너를 재시작하지 않는다.
정기 발송도 Solapi `sent_success`를 확인한 뒤에만 성공 fingerprint로 중복 제외한다.
공급사 등록 전에 실패 상태의 attempt receipt를 먼저 기록하고, 접수 응답의 group ID는
최종 조회 전에 즉시 저장한다. timeout/pending group은 2일 incident 보존 기간 동안 매
주기 `updated_at`이 오래된 순서로 최대 10건씩 재조회하고, 조회한 미확정 건은 순번의
뒤로 보내 공정하게 순환한다. 결과 미확정 또는 group ID 유실 attempt의 fingerprint는 자동
재발송하지 않고 `/dev` 운영 위험으로 남긴다. 확정 실패만 5분 cooldown 후 재시도하며,
정기/수동 `user_incidents` 실행을 합쳐 시간당 provider 시도는 12건을 넘지 않는다.
상한에 걸린 fingerprint는 소비하지 않고 다음 주기에 다시 평가한다. 명시적
`--test-sms`와 CloudWatch alarm-transition 경로는 별도 검증/비상 신호이며 이 quota에
포함하지 않는다.

진단 시 `OpsAuditLog`의 `user_incident.*`, `alerts.user_incident_sms`,
`alerts.user_incident_sms_test`, `alerts.external_signal_sms`,
`cron.check_dev_alerts` action과 provider group id를 함께 확인한다.

---

## 1. API 500 급증

### 증상
- 사용자가 "서버 오류" 화면을 보고 보고
- `/health` 또는 `/healthz` 실패
- CloudWatch에서 5xx 급증

### 즉시 확인 (30초)
```bash
# 헬스체크
curl -s https://api.hakwonplus.com/healthz
curl -s https://api.hakwonplus.com/health

# 최근 배포 확인
gh run list -w "v1-build-and-push-latest.yml" -L 3
```

### 즉시 조치 (5분)

**경우 A: /healthz 실패 (앱 자체 다운)**
```bash
# ASG 인스턴스 상태 확인
RUN_ENV aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-api-asg \
  --query "AutoScalingGroups[0].Instances[*].{Id:InstanceId,Health:HealthStatus,State:LifecycleState}" \
  --output table

# 인스턴스가 Unhealthy → ASG가 자동 교체함. 기다린다.
# 인스턴스가 없음 → ASG min 확인
RUN_ENV aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-api-asg \
  --query "AutoScalingGroups[0].{Min:MinSize,Desired:DesiredCapacity}" \
  --output table
```

**경우 B: /healthz 200, /health 실패 (DB 연결 문제)**
→ **§ 5. DB 장애** 참조

**경우 C: 최근 배포 직후 발생**
→ **§ 6. 배포 실패/롤백** 참조 (이미지 롤백)

### 복구 확인
```bash
curl -s https://api.hakwonplus.com/healthz   # 200
curl -s https://api.hakwonplus.com/health     # 200
```

### 에스컬레이션 기준
- 5분 내 자동 복구 안 되면 롤백 실행
- 롤백 후에도 복구 안 되면 AWS Support 케이스 오픈

---

## 2. 메시징 중복/미발송

### 증상
- 학부모가 같은 문자를 여러 번 수신
- 발송 예정 메시지가 미발송

### 즉시 확인 (30초)
```bash
# SQS 큐 깊이 확인
RUN_ENV aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/{ACCOUNT}/academy-v1-messaging-queue \
  --attribute-names ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible \
  --output table

# DLQ 확인
RUN_ENV aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/{ACCOUNT}/academy-v1-messaging-queue-dlq \
  --attribute-names ApproximateNumberOfMessages \
  --output text

# 워커 인스턴스 확인
RUN_ENV aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-messaging-worker-asg \
  --query "AutoScalingGroups[0].Instances[*].{Id:InstanceId,Health:HealthStatus,State:LifecycleState}" \
  --output table
```

### 즉시 조치 (5분)

**미발송 (큐에 메시지 쌓임):**
```bash
# 워커 살아있는지 확인 — 인스턴스 0개면:
RUN_ENV aws autoscaling set-desired-capacity \
  --auto-scaling-group-name academy-v1-messaging-worker-asg \
  --desired-capacity 1
```

**중복 발송:**
- SQS visibility timeout이 처리 시간보다 짧으면 중복 발생 가능
- 워커 로그에서 동일 message_id 처리 여부 확인
- 근본 원인 파악 전까지 **워커를 중지하지 말 것** (미발송이 중복보다 위험)

**DLQ에 메시지 있음:**
- DLQ 메시지 내용 확인 후 원인 파악
- 단순 일시 오류면 DLQ에서 원래 큐로 재전송 (AWS 콘솔 > SQS > DLQ redrive)

### 복구 확인
```bash
# 큐 깊이 0으로 수렴 확인
RUN_ENV aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/{ACCOUNT}/academy-v1-messaging-queue \
  --attribute-names ApproximateNumberOfMessages --output text
```

### 에스컬레이션 기준
- DLQ 메시지 10개 이상 누적
- 30분 이상 큐 깊이 감소하지 않음

---

## 3. 영상 인코딩 실패

### 증상
- 관리자가 업로드한 영상이 "처리 중" 상태에서 멈춤
- 영상 상태가 FAILED

### 즉시 확인 (30초)
```bash
# Django 관리 명령으로 stuck 영상 확인
RUN_ENV aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets "Key=tag:Name,Values=academy-v1-api" \
  --parameters 'commands=["docker exec academy-api python manage.py scan_stuck_video_jobs"]' \
  --output text --query "Command.CommandId"

# 위 명령 결과 확인 (CommandId 사용)
RUN_ENV aws ssm get-command-invocation \
  --command-id {COMMAND_ID} \
  --instance-id {INSTANCE_ID} \
  --query "StandardOutputContent" --output text
```

### 즉시 조치 (5분)

**PENDING 상태에서 멈춘 영상 (stuck):**
```bash
# stuck 영상 복구 (PENDING → NEW로 리셋)
RUN_ENV aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets "Key=tag:Name,Values=academy-v1-api" \
  --parameters 'commands=["docker exec academy-api python manage.py recover_stuck_videos"]' \
  --output text
```

**FAILED 상태 영상 재시도:**
```bash
# 실패 영상 재인큐
RUN_ENV aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets "Key=tag:Name,Values=academy-v1-api" \
  --parameters 'commands=["docker exec academy-api python manage.py enqueue_uploaded_videos --include-failed"]' \
  --output text
```

**비디오 데몬 워커 자체가 죽은 경우:**
- 비디오 워커는 전용 인스턴스에서 동작 (ASG 아님, 별도 관리)
- SSH 접속하여 docker 컨테이너 상태 확인

### 복구 확인
- 관리자 페이지에서 영상 상태가 COMPLETED로 변경 확인
- 학생 앱에서 영상 재생 가능 확인

### 에스컬레이션 기준
- 같은 영상이 3회 이상 실패
- 30분 이상 경과 후에도 PENDING 상태

---

## 4. 비용 급증

### 증상
- AWS Billing 알림 수신
- 예상 비용 대비 비정상적 증가

### 즉시 확인 (30초)
```bash
# EC2 실행 중인 인스턴스 수 확인
RUN_ENV aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" "Name=tag-key,Values=Name" \
  --query "Reservations[*].Instances[*].{Name:Tags[?Key=='Name']|[0].Value,Type:InstanceType,State:State.Name}" \
  --output table

# ASG desired capacity 확인
RUN_ENV aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-api-asg academy-v1-messaging-worker-asg academy-v1-ai-worker-asg \
  --query "AutoScalingGroups[*].{Name:AutoScalingGroupName,Min:MinSize,Desired:DesiredCapacity,Max:MaxSize,Running:length(Instances)}" \
  --output table
```

### 즉시 조치 (5분)

**ASG가 max까지 스케일아웃된 경우:**
```bash
# 원인 파악: 최근 스케일링 활동 확인
RUN_ENV aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name academy-v1-api-asg \
  --max-items 5 --output table
```
- CPU/메모리 부하 원인 파악 후 해결
- 의도하지 않은 스케일아웃이면 desired를 min으로 재설정

**Batch 작업이 과다 실행:**
```bash
# 활성 Batch 작업 확인
RUN_ENV aws batch list-jobs --job-queue academy-v1-video-batch-queue --job-status RUNNING --output table
```

**ECR 이미지 과다 누적:**
```bash
# 이미지 수 확인
RUN_ENV aws ecr describe-repositories \
  --query "repositories[*].{Name:repositoryName}" --output table

# 특정 repo 이미지 수
RUN_ENV aws ecr list-images --repository-name academy-api \
  --query "length(imageIds)" --output text
```
- lifecycle policy 적용 여부 확인 → 미적용이면 즉시 적용

### 복구 확인
- 인스턴스 수가 정상 수준으로 복귀
- 다음 날 billing 추이 확인

### 에스컬레이션 기준
- 일일 비용이 평소 2배 이상
- 원인 불명의 인스턴스 증가

---

## 5. DB 장애

### 증상
- `/healthz` 200이지만 `/health` 실패 (DB 연결 불가)
- API 응답에서 "database" 관련 에러
- 모든 데이터 조회 실패

### 즉시 확인 (30초)
```bash
# 헬스 엔드포인트로 DB 상태 확인
curl -s https://api.hakwonplus.com/health

# RDS 상태 확인
RUN_ENV aws rds describe-db-instances \
  --query "DBInstances[*].{Id:DBInstanceIdentifier,Status:DBInstanceStatus,Class:DBInstanceClass}" \
  --output table

# 연결 예산 경보 확인
RUN_ENV aws cloudwatch describe-alarms \
  --alarm-names academy-rds-DatabaseConnectionsHigh \
  --query "MetricAlarms[0].{State:StateValue,Reason:StateReason}" \
  --output table
```

### 즉시 조치 (5분)

**RDS 상태가 "available"이 아닌 경우:**
- `modifying` → 진행 중인 변경 완료 대기
- `backing-up` → 자동 백업 중. 일시적. 대기
- `storage-full` → RDS 스토리지 즉시 증설:
  ```bash
  RUN_ENV aws rds modify-db-instance \
    --db-instance-identifier {DB_INSTANCE_ID} \
    --allocated-storage {NEW_SIZE_GB} \
    --apply-immediately
  ```

**RDS 정상인데 연결 실패:**
- Security Group 규칙 변경 여부 확인
- API 인스턴스의 네트워크(VPC/서브넷) 확인
- 최근 인프라 변경이 있었는지 확인
- 로그에 `remaining connection slots are reserved`가 있으면 RDS를
  재부팅하지 말고 먼저 API 컨테이너의 PostgreSQL 소켓 점유와
  `DB_CONN_MAX_AGE` readback을 확인한다.
- 단일 API 컨테이너가 연결을 포화시키고 `DB_CONN_MAX_AGE`가 `0`이
  아니면 `/academy/api/env`의 해당 키만 `0`으로 변경하고
  `pwsh scripts/v1/refresh-api-env.ps1 -AwsProfile default`로
  롤백 보호 교체를 실행한다. SecureString 전체 값은 터미널이나
  보고서에 출력하지 않는다.
- SSM readback, `/healthz`, database-backed `/health`, API 컨테이너의
  PostgreSQL 소켓 감소를 모두 확인한다. 설정이 이미 `0`인데 포화가
  반복되면 관리자 연결로 `pg_stat_activity`의 사용자·클라이언트·상태
  분포를 수집하고 점유 서비스를 격리한다.

**RDS 재시작 필요 시:**
```bash
RUN_ENV aws rds reboot-db-instance \
  --db-instance-identifier {DB_INSTANCE_ID}
```
> 주의: 재시작은 1-5분 다운타임 발생. 최후의 수단.

### 복구 확인
```bash
curl -s https://api.hakwonplus.com/health   # 200 + "database": "connected"
```

`/healthz`는 DB를 확인하지 않으므로 단독 복구 증거로 사용하지 않는다.
`academy-rds-DatabaseConnectionsHigh`는 설정된 평가 기준에 따라 `OK`로
복귀할 때까지 감시한다.

### 에스컬레이션 기준
- RDS 상태가 10분 이상 비정상
- storage-full이 반복 발생
- 데이터 손실 의심

---

## 6. 배포 실패/롤백

### 증상
- GitHub Actions 워크플로우 실패
- 배포 후 `/healthz` 또는 `/health` 실패
- 배포 후 기능 장애

### 즉시 확인 (30초)
```bash
# CI/CD 실행 상태
gh run list -w "v1-build-and-push-latest.yml" -L 3

# 실패한 run 로그
gh run view --log-failed

# 현재 헬스 확인
curl -s https://api.hakwonplus.com/healthz
curl -s https://api.hakwonplus.com/health
```

### 즉시 조치 (5분)

**경우 A: 빌드/푸시 실패 (이미지가 바뀌지 않음)**
- 코드 수정 후 다시 push. 기존 서비스에 영향 없음.

**경우 B: Migration 실패**
- 배포가 자동 중단됨 (deploy-api가 migration에 의존)
- migration 오류 수정 후 다시 push

**경우 C: 배포 후 서비스 장애 → 롤백 필요**

API와 Messaging은 신규 결제·메시징 상태를 구버전 바이너리가 오해할 수 있고
ASG 교체 중 writer를 완전히 멈출 수 없어 image-only rollback을 실행 전에
`STATEFUL_IMAGE_ROLLBACK_BLOCKED`로 차단한다. 원하는 소스를
revert/cherry-pick한 새 커밋으로 빌드·배포하는 roll-forward가 복구 절차다.
AI/Tools/Video처럼 state-machine 호환성 경계가 분리된 서비스만 아래 digest
rollback 절차를 사용한다.

```powershell
# API 예시는 정책을 출력하고 mutation 없이 차단된다.
pwsh scripts/v1/rollback-api.ps1 -AwsProfile default

# 지원 서비스는 -Sha 생략 시 바로 이전 digest, 명시 시 tag -> digest를 검증한다.
pwsh scripts/v1/rollback-ai.ps1 -Sha sha-XXXXXXXX -AwsProfile default
```

롤백 스크립트는 `:latest`를 읽거나 재태깅하지 않는다. 실제 LT/Batch digest에서 출발해 이전 digest를 선택하고, ASG refresh의 `Successful` 종결과 모든 InService 컨테이너 digest를 검증하지 못하면 실패한다.

- API/Messaging: image rollback 차단, 새 immutable image roll-forward
- AI: `rollback-ai.ps1`
- Tools: `rollback-tools.ps1`
- Video Batch: `rollback-video.ps1` (8개 필수 job definition과 CE를 함께 검증)

**Migration rollback:** 기본 금지. 신규 상태값이나 데이터 backfill을 구버전 schema로
되돌리면 데이터 손실과 old/new binary 혼재가 발생할 수 있다. migration별로 검증된
reverse contract, writer quiesce, RDS snapshot, dry-run 결과가 모두 있는 별도 runbook이
없는 경우 schema도 수정 migration을 새로 배포하는 roll-forward로 복구한다. 일반
incident 대응에서 임의의 `manage.py migrate {이전 번호}`를 실행하지 않는다.

### 복구 확인
```bash
# ASG refresh 완료 대기 (5-10분)
RUN_ENV aws autoscaling describe-instance-refreshes \
  --auto-scaling-group-name academy-v1-api-asg \
  --query "InstanceRefreshes[0].{Status:Status,Progress:PercentageComplete}" \
  --output table

# 헬스 확인
curl -s https://api.hakwonplus.com/healthz   # 200
curl -s https://api.hakwonplus.com/health     # 200
```

### 에스컬레이션 기준
- 롤백 후에도 서비스 복구 안 됨
- ASG instance refresh가 실패 또는 10분 이상 진행 없음
