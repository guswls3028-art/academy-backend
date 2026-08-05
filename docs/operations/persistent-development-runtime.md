# 상시 개발 런타임

상시 개발 런타임은 운영 릴리스 후보를 실제 운영 자원에 넣기 전에 검토하는 첫 번째 차단
게이트다. 운영과 같은 API 인스턴스 유형·AMI와 동일한 digest-pinned API/Tools/AI 이미지를
사용하지만, 운영 ASG·ALB·DB 사용자·큐·R2 버킷에는 연결하지 않는다.

## 자원 경계

- EC2 이름은 `academy-v1-api-development`이며 외부 inbound가 없는 전용 보안그룹을 쓴다.
  접속은 Session Manager와 `connect-api-development.ps1`의 로컬 포트 포워딩으로만 한다.
- 인스턴스 역할 `academy-api-development-role`은 개발 환경 파라미터, API/Tools ECR pull,
  개발 전용 SQS 세 큐에만 접근한다. 운영 SSM 파라미터와 운영 큐 권한은 없다.
- PostgreSQL은 기존 RDS 서버 안의 `academy_api_development` 데이터베이스와
  `academy_api_development_app` 역할을 쓴다. 이 역할은 운영 DB의 `CONNECT` 권한이 없다.
- 객체 저장소는 Cloudflare R2의 `academy-development-artifacts` 버킷만 사용한다.
  자격증명은 `/academy/r2/development/credentials` SecureString에 있고 운영 R2 버킷
  접근은 검증 단계에서 거부되어야 한다. AWS S3 버킷은 만들거나 사용하지 않는다.
- worker 설정도 `R2_STORAGE_BUCKET`·`R2_ADMIN_BUCKET`·`R2_REGION`을 worker env에서
  명시적으로 읽는다. API에는 개발 버킷이 적용됐지만 worker가 운영 기본 버킷명으로
  되돌아가 HWP 문항/해설 저장이 `AccessDenied`로 끝나는 경로를 허용하지 않는다.
- Redis/Valkey는 개발 EC2의 로컬 서비스다. 운영 Redis 엔드포인트를 공유하지 않는다.
- Tools worker와 AI worker는 같은 EC2 안의 별도 `academy-tools-development`,
  `academy-ai-development` 컨테이너로 실행되며 각각 개발 전용 큐만 소비한다.
  개발 AI worker는 운영 ASG scale-in 제어를 비활성화한다. 운영 Tools/AI ASG의
  평시 용량은 변경하지 않는다.
- 알림톡은 mock/dry-run, 자동 결제와 외부 알림 발송은 비활성화한다.

## 최초 구성

Cloudflare R2 전용 토큰을 만든 뒤
`/academy/r2/development/credentials`에 아래 키를 SecureString JSON으로 저장한다.
값은 로그, 문서, GitHub 출력에 남기지 않는다.

```text
R2_ENDPOINT
R2_REGION
R2_ACCESS_KEY
R2_SECRET_KEY
R2_BUCKET
```

그 다음 루트가 아닌 전용 운영자 자격증명으로 한 번만 실행한다.

```powershell
pwsh scripts/v1/initialize-api-development.ps1 -AwsProfile <least-privilege-profile>
```

이 명령은 전용 IAM·보안그룹·큐·DB 역할/DB를 수렴하고, 마지막으로 검증 완료 운영
manifest의 API/Tools digest를 사용해 첫 개발 인스턴스를 만든다. 인스턴스는 사용자가
명시적으로 폐기하기 전까지 유지한다.

계정 루트 ARN은 `Assert-AwsMutationIdentity`에서 차단한다. CI와 일반 배포는
`academy-gha-ecr-build` GitHub OIDC 역할만 사용하며 장기 AWS access key를 요구하지 않는다.
개발 권한은 기존 운영 inline 정책과 분리된 고객 관리형
`academy-gha-development-deploy` 정책으로 관리하며
`converge-api-development-oidc.ps1`이 backend main-ref와 승인된 production
environment 두 subject만 허용하는 trust와 정책 readback을 강제한다. 환경 없는
development job은 main-ref subject를 사용하고, production environment가 붙은
lock/mutation job은 environment subject를 사용한다.

## 릴리스 순서

백엔드 릴리스는 아래 순서를 건너뛸 수 없다.

1. GitHub Actions가 run-unique 태그로 immutable 후보 이미지를 빌드한다.
2. `publish-api-development-env.ps1`이 운영 형태의 값을 복사하되 DB·큐·R2·발송/결제를
   개발 경계로 치환한다.
3. `deploy-api-development.ps1`이 새 candidate 인스턴스를 만들고 migration, DB 역할,
   운영 DB 접근 거부, 개발 큐, 개발 R2와 운영 R2 접근 거부, Redis, `/healthz`,
   `/health`, 정확한 API/Tools/AI digest를 검증한다.
4. `run-api-development-smoke.ps1`이 합성 학생 XLSX 파싱, 1장 PPTX 생성·재열기,
   API와 AI worker 설정 경로 각각에서 개발 R2 객체 put/get/delete를 실행하고
   각 처리시간을 기록한다.
5. 모든 검증이 성공한 뒤에만 candidate를 active로 승격하고 이전 개발 인스턴스를
   종료한다. 실패하면 candidate만 종료하고 기존 active 인스턴스를 보존한다.
6. 이어서 별도 임시 preprod EC2 게이트가 통과해야 한다.
7. 그 뒤에만 운영 migration과 ASG/ALB 무중단 교체를 허용한다.

## 사용과 확인

```powershell
pwsh scripts/v1/connect-api-development.ps1 -AwsProfile <profile>
```

스크립트가 선택한 active 인스턴스의 API를 로컬 포트로 전달한다. 요청에는 반드시
`X-Tenant-Code`를 명시하며 기본 테넌트 추론은 없다.

정상 상태의 최소 증거는 다음과 같다.

- active 개발 인스턴스가 정확히 1대이고 종료 방지가 켜져 있음
- 보안그룹 inbound 0개, ALB/운영 ASG 등록 0개
- API, Tools, AI 컨테이너가 동일 release ID의 digest-pinned 이미지로 실행 중
- 개발 DB 현재 사용자/DB 일치 및 운영 DB `CONNECT=false`
- 개발 SQS 세 큐 조회 성공
- API·AI worker의 개발 R2 객체 round-trip 성공, 운영 R2 버킷 접근 거부
- 합성 XLSX 파싱과 PPTX 생성·재열기 성공 및 처리시간 제한 통과
- `/healthz`, `/health`, 로컬 Redis/Valkey `PONG`

개발 검토 중 실패는 운영 배포 차단 사유다. 개발 게이트를 skipped/success 이외의 상태로
우회하거나 후보를 운영 인스턴스에서 먼저 시험하지 않는다.
