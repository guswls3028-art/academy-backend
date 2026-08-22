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
  평시 용량은 변경하지 않는다. API enqueue 경로도 `ACADEMY_RUNTIME_ENV`가
  `development` 또는 `preprod`이면 운영 worker ASG capacity ensure를 호출하지 않고
  격리 런타임의 로컬 worker 계약을 사용한다. 해당 인스턴스 역할의 운영 ASG 접근 거부는
  계속 필수 경계다.
- Video Batch는 아직 이 상시 개발 런타임의 구성 자원이 아니다. 개발 API·worker env의
  `VIDEO_BATCH_JOB_QUEUE`와 `VIDEO_BATCH_JOB_DEFINITION`은 빈 값으로 고정하고 settings와
  배포 readback에서도 이를 강제한다. 따라서 업로드 완료 후 Batch 제출은 누락 설정 오류로
  실패 폐쇄하며 운영 `academy-v1-video-batch-*` 큐·job definition으로 흘러가지 않는다.
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
   현재 이 smoke는 Video Batch 제출 성공을 증명하지 않는다. Video Batch는 전용 개발
   queue/job definition/job role과 Batch에서 접근 가능한 개발 Redis가 준비되기 전까지
   비활성 상태여야 한다.
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

사용자 로그인이 필요한 시각 QA는 운영 테넌트에 테스트 계정을 남기지 않고 이
런타임에서 수행한다. 검수 대상 frontend의 exact checkout을 로컬에서 빌드하거나
실행하고 API proxy를 위 loopback tunnel로 지정한다. 검수용 tenant·교사·학생은
`setup_ymath_realuse_scenario`처럼 production DB/R2에서 실행을 거부하는 명령으로만
만든다. 실제 학생·학부모·성적·연락처와 운영 비밀값은 복제하지 않는다.

검수는 desktop과 390px에서 로그인, 대상 화면 DOM, 상호작용, 새로고침 후 상태,
가로 overflow와 콘솔/API 오류를 확인한다. 종료 시 같은 명령의 `--destroy`로 정확한
`qa-*` tenant를 삭제하고 출력의 `remaining`이 모두 0인지 확인해야 한다. setup,
검수, cleanup 중 하나라도 실패하면 완료로 기록하지 않는다. 상세 Ymath 절차는
[Ymath 실자료 원본 전수 검증](runbooks/ymath-real-source-qa.md)을 따른다.

정상 상태의 최소 증거는 다음과 같다.

- active 개발 인스턴스가 정확히 1대이고 종료 방지가 켜져 있음
- 보안그룹 inbound 0개, ALB/운영 ASG 등록 0개
- API, Tools, AI 컨테이너가 동일 release ID의 digest-pinned 이미지로 실행 중
- 개발 DB 현재 사용자/DB 일치 및 운영 DB `CONNECT=false`
- 개발 SQS 세 큐 조회 성공
- API·AI worker의 개발 R2 객체 round-trip 성공, 운영 R2 버킷 접근 거부
- 합성 XLSX 파싱과 PPTX 생성·재열기 성공 및 처리시간 제한 통과
- `/healthz`, `/health`, 로컬 Redis/Valkey `PONG`
- Video Batch queue/job definition이 빈 값이고 운영 Batch 제출이 불가능함

## Video Batch 개발 canary 준비 계약 [PROPOSED]

전용 development Video Batch 자원이 준비될 때 추가할 canary는 production 이름을
거부하고 다음 체인을 한 번의 disposable job으로 증명해야 한다. 현재 구현 및 릴리스
workflow 연결은 없다.

1. 상시 개발 API 컨테이너의 instance role로 `SubmitJob`을 호출한다.
2. Batch job role로 `/academy/workers/development/...` SecureString을 읽는다.
3. worker가 `academy_api_development` DB/역할로 연결하고 개발 Redis set/get/delete를 한다.
4. `academy-development-*` R2 객체를 put/get/delete한다.
5. job `SUCCEEDED`와 R2 object 잔여 0을 확인한다. timeout job은 terminate 후 terminal
   상태를 확인하지 못하면 실패한다.

구현 entrypoint는 `scripts/v1/run-video-development-canary.ps1`로 고정하며 dedicated
queue/job definition, versioned workers env parameter, development bucket, expected DB
name/user와 release ID를 필수 인자로 받아야 한다. 전용 queue/job definition, 최소 권한
job role, Batch에서 접근 가능한 개발 Redis가 모두 구성되고 해당 도구가 실제로 통과하기
전에는 env의 Video Batch 두 값을 채우거나 workflow에 연결하지 않는다. 운영 queue/job
definition, 운영 worker env, 운영 R2를 대체재로 사용하지 않는다.

개발 검토 중 실패는 운영 배포 차단 사유다. 개발 게이트를 skipped/success 이외의 상태로
우회하거나 후보를 운영 인스턴스에서 먼저 시험하지 않는다.
