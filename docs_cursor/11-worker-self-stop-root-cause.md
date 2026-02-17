# Worker Self-Stop 루트 캐우스 분석 (100% 확정)

## 1. 확정 사실

### 1.1 ASG가 인스턴스를 교체한 이유

**Scaling Activity Cause:**
```
an instance was taken out of service in response to an EC2 health check 
indicating it has been terminated or stopped.
```

→ EC2가 **terminated** 또는 **stopped** 상태가 되어 ASG가 "비정상"으로 판단 후 교체.

### 1.2 StopInstances 호출 주체 (CloudTrail)

| User (호출자) | 의미 |
|---------------|------|
| `i-07c9015cc720853a0` | EC2 인스턴스 ID |
| `i-029c7696c1a743e9d` | EC2 인스턴스 ID |
| ... | 모두 인스턴스 ID |

→ **IAM 사용자 아님. Lambda 아님.**  
→ **EC2 인스턴스가 자기 자신을 Stop**하고 있음.

### 1.3 결론

| 항목 | 결론 |
|------|------|
| Lambda? | ❌ 아님 |
| ASG 정책/scale-in? | ❌ 아님 |
| HealthCheck 오류? | ❌ 아님 (원인은 Stop으로 인한 인스턴스 소실) |
| **Worker 내부 self-stop** | ✅ **100% 원인** |

---

## 2. 구조적 충돌

| 설정 | 값 |
|------|-----|
| ASG | Min=1, Desired=1 (항상 1대 유지) |
| Worker 코드 | `ec2.stop_instances(...)` 호출 |

**루프:**
1. Worker: 일 없음 → `StopInstances(자기 자신)` 호출
2. ASG: 인스턴스 사라짐 → 새 인스턴스 Launch
3. Worker: 또 일 없음 → Stop...
4. → **무한 반복** (terminated 수십 개 발생)

---

## 3. Self-Stop 코드 위치

| 파일 | 함수 | 호출 조건 |
|------|------|-----------|
| `academy/framework/workers/ai_sqs_worker.py` | `_stop_self_ec2()` | `IDLE_STOP_THRESHOLD > 0 and consecutive_empty >= IDLE_STOP_THRESHOLD` |
| `apps/worker/video_worker/sqs_main.py` | `_stop_self_ec2()` | `IDLE_STOP_THRESHOLD > 0 and consecutive_empty_polls >= IDLE_STOP_THRESHOLD` |
| `apps/worker/ai_worker/run.py` | `_stop_self_ec2()` | **무조건** (finally 블록에서 항상 호출) |

### 3.1 ASG에서 실제 실행되는 워커

| ASG | 이미지 | 진입점 | Self-stop guard |
|-----|--------|--------|-----------------|
| academy-ai-worker-asg | academy-ai-worker-cpu | `sqs_main_cpu` → ai_sqs_worker | `IDLE_STOP_THRESHOLD > 0` ✅ |
| academy-video-worker-asg | academy-video-worker | sqs_main | `IDLE_STOP_THRESHOLD > 0` ✅ |
| academy-messaging-worker-asg | academy-messaging-worker | messaging worker | self-stop 없음 |

### 3.2 User Data 설정

- `infra/worker_asg/user_data/*.sh` → `-e EC2_IDLE_STOP_THRESHOLD=0` 전달
- `EC2_IDLE_STOP_THRESHOLD=0` 이면 `IDLE_STOP_THRESHOLD > 0` 이 False → self-stop 미호출되어야 함

### 3.3 왜 아직도 Stop이 발생하는가?

1. **이전 이미지 사용**: full_redeploy 시 instance refresh 실패로 새 이미지가 ASG에 반영되지 않았을 수 있음.
2. **Git에 미푸시**: full_redeploy는 Git에서 clone 후 빌드 → 로컬 수정이 repo에 없으면 이전 코드가 빌드됨.
3. **SSM .env 덮어쓰기 가능성**: `/academy/workers/env`에 `EC2_IDLE_STOP_THRESHOLD=5` 등이 있으면 `-e EC2_IDLE_STOP_THRESHOLD=0`이 나중에 오므로 보통은 `-e`가 우선. (Docker: `-e` > `--env-file`)

---

## 4. 해결 방안

### 방법 1: Self-stop 비활성화 (코드·설정 기반, 권장)

**현재 코드 상태:**
- ai_sqs_worker, video sqs_main: `IDLE_STOP_THRESHOLD > 0` 조건으로 이미 guard 있음.
- user_data: `EC2_IDLE_STOP_THRESHOLD=0` 전달.

**필요 조치:**
1. 수정된 코드를 **Git에 push**.
2. **full_redeploy**로 새 이미지 빌드·ECR 푸시.
3. **instance refresh** 실행 (스크립트 수정 후 재실행).

```powershell
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG -SkipBuild
```

### 방법 2: IAM에서 ec2:StopInstances 차단 (즉시 안정화, 복붙용)

Worker IAM Role에 **Deny 정책**을 추가하여 `ec2:StopInstances` 호출을 차단.

- **효과**: 코드가 StopInstances를 호출해도 AccessDenied → 인스턴스 유지.
- **ASG 스케일 인**: 영향 없음 (ASG는 TerminateInstances 사용, 별도 서비스 권한).
- **운영**: ASG만 사용 시 안전.

**복붙 명령어 (PowerShell):**

```powershell
cd C:\academy
.\scripts\remove_ec2_stop_from_worker_role.ps1
```

**수동 CLI (스크립트 없이):**

```powershell
# Deny 정책 JSON 생성 후 적용
$policy = '{"Version":"2012-10-17","Statement":[{"Sid":"DenyStopInstances","Effect":"Deny","Action":"ec2:StopInstances","Resource":"*"}]}'
$policy | Out-File -FilePath "$env:TEMP\deny_stop.json" -Encoding utf8
aws iam put-role-policy --role-name academy-ec2-role --policy-name academy-deny-ec2-stop-instances --policy-document "file://$env:TEMP/deny_stop.json" --region ap-northeast-2
```

---

## 5. 추가 참고: run.py (API poll 기반)

`apps/worker/ai_worker/run.py`는 **finally 블록에서 무조건** `_stop_self_ec2()` 호출.

- ASG 워커는 `sqs_main_cpu` → ai_sqs_worker 경로 사용.
- `run.py`는 별도 single-run 모드용.
- ASG용 이미지에는 해당 경로가 사용되지 않음.

---

## 6. 요약

| 항목 | 내용 |
|------|------|
| 원인 | Worker 내부 self-stop 로직이 EC2를 종료 |
| 주체 | 인스턴스 자신 (CloudTrail Username = InstanceId) |
| 권장 조치 | 수정 코드 Git push → full_redeploy → instance refresh |
| 긴급 차단 | IAM에서 `ec2:StopInstances` 제거 |
