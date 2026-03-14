# Messaging/AI 워커 UserData 적용 보고서

**Generated:** 2026-03-07
**Video:** 제외 (이미 성공)

## 1. 작업 요약

### 1.1 워커 LT UserData 추가
- **원인**: Messaging/AI 워커 LT에 UserData 없음 → EC2 부팅 시 Docker·워커 컨테이너 미실행
- **조치**:
  - `scripts/v1/resources/worker_userdata.ps1` 신규 생성
  - `Get-LatestWorkerImageUri`, `Get-WorkerLaunchTemplateUserData` 함수
  - SSM `/academy/workers/env` (base64 JSON) → 디코딩 후 env 파일 생성
  - Messaging/AI LT에 UserData 주입 (Docker 설치 → ECR pull → SSM env → docker run)

### 1.2 기타 수정
- **redis.ps1**: `$sgId:` 변수 파싱 오류 수정 (`${sgId}:` 사용)
- **worker_userdata.ps1**: `${RepoName}:$tagToUse` PowerShell 파싱 오류 수정
- **asg_ai.ps1, asg_messaging.ps1**: InstanceRefreshInProgress 시 경고 후 계속 진행

## 2. 배포 결과

| ASG | LT 버전 | UserData | instance-refresh |
|-----|---------|----------|-----------------|
| academy-v1-ai-worker-asg | v2 | ✅ 적용 | 이미 진행 중 → 경고 후 계속 |
| academy-v1-messaging-worker-asg | v2 | ✅ 적용 | 이미 진행 중 → 경고 후 계속 |

## 3. 인프라 연결·정합성

### 3.1 SSOT ↔ Actual
| 항목 | SSOT | Actual | 일치 |
|------|------|--------|------|
| Messaging SQS | academy-v1-messaging-queue | academy-v1-messaging-queue | Yes |
| AI SQS | academy-v1-ai-queue | academy-v1-ai-queue | Yes |
| Messaging VisibilityTimeout | 900 | 900 | Yes |
| AI VisibilityTimeout | 1800 | 1800 | Yes |
| API/AI/Messaging ASG min/desired | 1/1 | 1/1 | PASS |

### 3.2 현재 상태
- **Messaging 워커 인스턴스** (i-0dc5242f7d8e37c76): IamProfile=null (구 인스턴스)
- **instance-refresh**: InProgress (22:32 UTC 시작)
- **SQS 소비**: instance-refresh 완료 후 신규 인스턴스(IamProfile+UserData)로 소비 예상

## 4. 후속 확인

1. **instance-refresh 완료 대기** (scale-in protection으로 10~15분 소요 가능)
2. **SQS 테스트**: academy-v1-messaging-queue에 메시지 발송 후 30초 내 소비 여부 확인
3. **신규 인스턴스 확인**: IamInstanceProfile, UserData 로그(/var/log/academy-worker-userdata.log)

## 5. 관련 파일

- `scripts/v1/resources/worker_userdata.ps1` — 워커 UserData 생성
- `scripts/v1/resources/asg_messaging.ps1` — Messaging LT UserData 주입
- `scripts/v1/resources/asg_ai.ps1` — AI LT UserData 주입
- `scripts/v1/resources/redis.ps1` — 변수 파싱 수정
