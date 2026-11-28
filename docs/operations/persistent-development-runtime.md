# 상시 개발 런타임

상시 개발 런타임은 운영 릴리스 후보를 실제 운영 자원에 넣기 전에 검토하는 첫 번째 차단
게이트다. 운영과 같은 API 인스턴스 유형·AMI와 동일한 digest-pinned API/Tools/AI 이미지를
사용하지만, 운영 ASG·ALB·DB 사용자·큐·R2 버킷에는 연결하지 않는다.

## 자원 경계

- EC2 이름은 `academy-v1-api-development`이며 외부 inbound가 없는 전용 보안그룹을 쓴다.
  접속은 Session Manager와 `connect-api-development.ps1`의 로컬 포트 포워딩으로만 한다.
- 새 개발 candidate의 루트 볼륨은 `apiDevelopment.rootVolumeSizeGb`(20 GiB)를 쓴다.
  API·Tools·Messaging·AI를 한 호스트에 풀면서 8 GiB AMI 루트가 AI 레이어 추출 중
  `no space left on device`로 실패한 경계를 해결하기 위한 개발 전용 여유 공간이다.
  AMI·인스턴스 유형은 계속 운영과 같고, `DescribeImages`에서 확인한 EBS 루트 장치에만
  크기와 `DeleteOnTermination=true`를 덮어쓴다. AMI snapshot·볼륨 유형·성능 및
  암호화/account 기본값은 그대로 상속하며 별도 KMS·볼륨 태그 권한을 추가하지 않는다.
  크기는 정수 16–1024 GiB이며 AMI 루트보다 작으면 launch 전에 실패한다.
  기존 active/volume은 수정하지 않고 모든 개발 검증 통과 후에만 이전 active를 교체한다.
  실패 candidate는 기존 cleanup 계약으로 종료한다. 적용 후 루트 크기·파일시스템 여유와
  네 컨테이너 및 실사용 smoke를 확인하며 크기 설정만으로 성공을 선언하지 않는다.
  검증: `python -B -m unittest scripts.v1.test_development_storage -v`와 기존 개발 런타임 계약.
- 인스턴스 역할 `academy-api-development-role`은 개발 환경 파라미터, API/Tools ECR pull,
  개발 전용 SQS 세 큐만 Allow 대상으로 삼는다. 관리형 SSM 정책의 광역 parameter
  Allow는 아래 explicit deny로 별도 제한해야 하며 실제 적용 여부는 readback한다.
- PostgreSQL은 기존 RDS 서버 안의 `academy_api_development` 데이터베이스와
  `academy_api_development_app` 역할을 쓴다. 이 역할은 운영 DB의 `CONNECT` 권한이 없다.
- 객체 저장소는 Cloudflare R2의 `academy-development-artifacts` 버킷만 사용한다.
  자격증명은 `/academy/r2/development/credentials` SecureString에 있고 운영 R2 버킷
  접근은 검증 단계에서 거부되어야 한다. AWS S3 버킷은 만들거나 사용하지 않는다.
  영상 멀티파트 실사용 진단에는 `academy-dev-native-multipart-qa` CORS 규칙을 둔다.
  `http://localhost:4173`의 `PUT`과 요청 헤더 `Content-Type`을 허용하고 `ETag`를 노출한다.
  Cloudflare 정책 API로 적용할 때 기존 Range 재생용 `GET`·`HEAD` 규칙을 보존하고 다시 읽는다.
  격리 브라우저에서 서명된 조각 업로드, 응답 유실 후 같은 조각 재시도, 완성 객체의 크기와
  열린 멀티파트 업로드·테스트 데이터 정리 0건을 확인한다.
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
  배포 readback에서도 이를 강제한다. 업로드가 완료되어도 인코딩 대상으로 사용할 큐와 job
  definition이 없으면 신규 job과 잠금 전에 `VIDEO_BATCH_SUBMIT_FAILED`를 반환한다.
  영상은 `FAILED`로 저장하고 원본 객체를 보존한다. 필요한 설정이 갖춰지면 기존 재처리 경로를 쓴다.
- 운영 `CDN_HLS_SIGNING_SECRET`은 개발 env에 복사하거나 다른 개발 credential에서 파생하지
  않는다. env publication은 매 릴리스마다 암호학적 난수 32바이트로 개발 전용 키를 만들고,
  운영 키와 다름을 확인한 뒤 API와 worker에 같은 `CDN_HLS_SIGNING_SECRET`과 key id `v1`을
  넣는다. 두 versioned parameter의 exact equality는 값 노출 없이 readback한다. 이 키는 운영
  CDN에서 신뢰되지 않으며, frontend 실사용 카나리는 playback/session 응답으로 받은 exact
  signed URL만 메모리 HLS·poster fixture에 연결한다. 누락되면 unsigned URL을 허용하지 않고
  릴리스를 실패시킨다. API와 worker settings가 각각 canonical CDN·키 길이·key id를
  fail-closed로 검사하고, 개발 배포는 API·Tools·AI 컨테이너의 키 fingerprint가 모두 같은지도
  값 노출 없이 확인한다.
- 알림톡은 mock/dry-run, 자동 결제와 외부 알림 발송은 비활성화한다.

### SSM parameter 권한 경계

`AmazonSSMManagedInstanceCore`의 `GetParameter`/`GetParameters` 전체 자원 허용은
개발 전용 Allow 문만 추가해서는 제한되지 않는다. `Ensure-ApiDevelopmentIAM`은
`templates/iam/policy_api_development_parameter_boundary.json`의 explicit deny를
재구성한 `academy-api-development-runtime` inline policy에 합친다. 이 전체 bootstrap은
trust·managed attachment·profile도 수렴하므로 기존 host의 deny-only 교정에 사용하지
않는다. 아래 좁은 적용기만 기존 inline의 모든 필드·문장을 보존한다. API env, workers
env, 개발 DB credentials, 개발 QA password, 개발 R2 credentials의 정확한 다섯
parameter ARN만 개별 조회할 수 있고, 그 밖의 parameter 조회와 모든
`GetParametersByPath`/`GetParameterHistory`를 거부한다. 상위 경로 재귀 조회나
접두사가 비슷한 경로를 예외로 인정하지 않는다. SSM managed attachment와
agent의 control/data channel 권한은 유지한다.

이미 존재하는 역할에 이 deny가 적용됐다고 소스 변경만으로 판단하지 않는다.
`python scripts/v1/converge_frontend_development_qa.py`는 기존 inline/managed
grant 전체와 제안 deny의 양성/음성 IAM 시뮬레이션을 출력하는 **읽기 전용**
계획이다. 기본 실행과 `--frontend-role-plan`은 IAM 및 잠금 쓰기를 하지 않는다.
운영 parameter 값 조회로 거부를 시험하지 않는다.
IAM 시뮬레이션은 KMS key policy, 조직 SCP 등을 포함한 실제 복호화 성공/실패의
증거가 아니다. 검토된 최소 권한 수렴 및 실제 정책 readback 전에는 새 synthetic
QA를 실행하지 않는다. frontend 전용 OIDC 역할의 도입은 이 개발 host 역할 교정과
분리해 검토하며 backend production 역할의 신뢰나 권한을 넓히지 않는다.

시험지 변환의 실제 OCR·수정 제안·그림 재작성 QA는 개발 AI worker에서도 운영과
동일한 Nova 2 Lite 모델을 호출한다. `--problem-transcription`은 기존 두 parameter
deny가 정확히 적용된 host에 `policy_workers_bedrock_problem_transcription.json`의
고정 `bedrock:InvokeModel` Allow 하나만 추가하는 별도 선택이다. 현재 account의
`global.amazon.nova-2-lite-v1:0` profile과 그 모델의 지역별 foundation ARN만 허용한다.
다른 모델·account의 profile·streaming 호출은 허용하지 않는다. 기존 정책 필드,
trust, attachment, frontend OIDC 역할, 운영 역할과 데이터 격리는 유지한다.
정적 AI 키를 복사하지 않으며 합성 fixture와 일회용 QA tenant에만 호출한다.

#### host 단일 정책 적용과 공용 잠금

`converge_frontend_development_qa.py --apply-host-boundary`만 명시적 host 쓰기
모드다. 코드/테스트 완료는 AWS 적용 승인이 아니다. 별도로 승인된 exact 작업에서
새 계획의 `before_sha256`, `after_sha256`, `inventory_sha256`를 검토한 뒤
각각 아래 인자로 전달한다. 과거 계획에 inventory fingerprint가 없다면 다시
읽기 전용 계획을 만들며 임의로 hash를 채우지 않는다.

```powershell
python scripts/v1/converge_frontend_development_qa.py --aws-profile <approved-profile>
# 별도의 exact AWS 적용 승인 및 competing writer 배제 확인 후에만 실행:
python scripts/v1/converge_frontend_development_qa.py --aws-profile <approved-profile> --apply-host-boundary --expected-current-hash <before_sha256> --expected-proposed-hash <after_sha256> --expected-inventory-hash <inventory_sha256>
# 적용 후 재검증은 읽기 전용이며 잠금도 변경하지 않는다:
python scripts/v1/converge_frontend_development_qa.py --aws-profile <approved-profile> --verify-host-boundary --expected-proposed-hash <after_sha256> --expected-inventory-hash <inventory_sha256>
```

Nova 호출 권한의 계획·적용·검증은 위 각 명령에 `--problem-transcription`을 함께
전달한다. 기본 deny-only 모드와 혼용하지 않는다. 동일한 세 hash 검토와 단일 정책
쓰기, 공용 잠금, readback, parameter 양성/음성 검증에 모델 허용/거부 검증을 더한다.
이미 적용된 SID는 재작성하지 않고 검증 모드로 확인한다. 전체 bootstrap도 같은
Nova 정책을 포함하므로 이후 환경 재구성에서 이 권한을 누락하지 않는다.

적용기는 기존 `deployment_lock.py`와 `academy-v1-video-job-lock`의
`videoId=__deployment_control_v2__`를 재사용한다. 새 잠금/테이블/권한을 만들지
않는다. account `809466760795`, region `ap-northeast-2`, role
`academy-api-development-role`, policy `academy-api-development-runtime`만 대상으로
한다. IAM과 잠금 helper는 같은 named profile 전용 subprocess 환경을 사용한다.
상충하는 정적 credential 환경변수는 자식 환경에서만 제거하며 값을 출력하지 않는다.
다른 table/region override 및 inherited `ACADEMY_DEPLOY_LOCK_OWNER` /
`ACADEMY_RUNTIME_ENV_LOCK_OWNER`는 거부한다. 공용 acquire는 재진입 불가이므로
같은 owner의 활성 잠금도 재획득하거나 부모 잠금으로 간주하지 않는다.

잠금 획득 후 policy와 inventory hash를 다시 검사한다. inventory에는 role ID/EC2
trust, permissions-boundary 부재, inline 1개, SSM managed grant 1개의 문서/버전,
profile ID/role binding, 연결된 유일한 running·active·종료 방지 개발 instance와
고정 scope 태그가 포함된다. inline의 기존 모든 필드·문장은 보존하고 검토한 두 Deny만
추가한다. 충돌/중복 Sid, 다른 grant/role/profile/instance, hash drift는 쓰기 전에
거부한다. 이미 적용된 policy는 다시 append하지 않고 별도 verifier를 사용한다.

쓰기 직전 lease renew/assert와 current hash를 재검사하고 정확한 `PutRolePolicy`
한 번만 호출한다. 그 외 IAM trust/attachment/profile, FE 역할, SSM, EC2/ASG/SQS
변경이나 전체 bootstrap/QA 실행은 없다. postverify는 policy/inventory 재조회와
**overlay 없는 현재 principal** 44개 action/resource 결과(allowed 19, explicitDeny
25, missing/duplicate/context 0)를 검사하고 simulation 뒤 policy/inventory를 다시
확인한다. 기존 plan의 `--policy-input-list` 제안 결과는 적용 증거로 사용하지 않는다.
자동 IAM 재시도와 자동 rollback은 없으며 전파 지연도 검증 실패로 처리한다.

기존 writer도 `initialize-api-development.ps1 → converge-api-development-prerequisites.ps1
→ Ensure-ApiDevelopmentIAM`의 host IAM 구간에서 같은 잠금을 직접 획득한다.
함수 자체는 실제 소유권을 각 IAM mutation 직전과 마지막 readback에서 검사하므로
잠금 없는 직접 호출도 거부한다. 기존 역할 및 최초 역할/profile 생성은 유지되며
PlanMode는 쓰기/잠금 없이 반환한다. 정상 host readback/소유권 검증 뒤 잠금을
반환한 다음 기존 OIDC·queue·DB 및 initializer의 publish/deploy 흐름을 계속한다.
이는 전체 initializer/SG/queue/DB/OIDC/standalone 개발 배포가 잠금으로 보호된다는
주장이 아니다. narrow Apply는 이 broad bootstrap을 호출하지 않는다.

획득 실패에는 release를 시도하지 않는다. 쓰기 전 실패이고 여전히 소유하면 반환하며,
쓰기 시도 이후 timeout/검증 실패는 한 번 더 소유권을 확인해 보류 상태와 만료 추정
시각을 보고한다. 소유권 상실이면 `ownership_unconfirmed`로 남기며 타인 item을
해제하지 않는다. release 실패도 성공으로 숨기지 않는다. Python 적용기의 JSON
checkpoint는 단계, owner, write-attempt 여부, 검증/잠금 상태를 구분하며 예외에
포함된 원문 CLI/비밀값은 출력하지 않는다. PowerShell bootstrap도 불확실한
획득/쓰기/소유권/반환을 구별하고 IAM native retry를 1회로 제한한다.

Python 적용은 600초 deadline의 checkpoint와 native 호출 30초 제한(직접 AWS
connect/read 5/15초, SDK retry 1회)을 사용한다. 기본 lease는 10,800초이며 보류는
TTL까지만 유효하다. 갑작스러운 프로세스 종료에는 checkpoint/finally가 실행되지
않을 수 있고 helper의 자식 프로세스 종료까지 보장하지 않는다. 이 잠금은 IAM의
fencing token이나 CAS가 아니다. 직접 CLI/console·구버전 writer·만료 후 살아 있는
프로세스의 check/write race를 제거하지 못하므로 승인된 작업 동안 경쟁 writer
배제와 수정된 writer 채택이 필요하다. old policy 복원은 광역 parameter 읽기를
재개하므로 자동 복구 대상이 아니며 별도 exact 승인/검증을 요구한다.

검증: `python -B -m unittest scripts.v1.test_frontend_development_qa -v`.
Backend Quality Gate의 기존 `Deployment contract tests` 단계도 같은 명령을 실행하며
0이 아닌 종료코드는 즉시 step 실패로 처리한다. 전체 pytest 수집과 별도인 회귀다.
기존 initializer/IAM/guard를 로컬 fake AWS로 실행하고, 실제 공용 잠금 알고리즘을
fake DynamoDB로 검사한다. 두 writer의 순서 교차·획득 실패·소유권 상실·commit 후
timeout·postverify 실패 및 기존 initializer의 존재/신규 경로를 검증한다. 이는 실제
AWS 경합·IAM 적용·SSM 세션·synthetic QA의 증거가 아니며 이들의 HOLD를 해제하지 않는다.

### frontend 동일 artifact real-use 진입점

`templates/iam/trust_frontend_development_qa.json`과
`policy_frontend_development_qa.json`은 별도 `academy-frontend-development-qa`
역할의 검토 대상 계약이다. frontend main-ref OIDC만 허용하며 backend production
역할과 기존 frontend R2 bootstrap 역할은 변경하지 않는다. 신규 역할/문서의 read-only
계획은 아래로 분리해 실행한다. frontend 역할/문서 Apply는 제공하지 않으며, 기존 같은 이름의
자원이 발견되면 덮어쓰지 않고 별도 검토를 요구한다.

```powershell
python scripts/v1/converge_frontend_development_qa.py --frontend-role-plan
```

`templates/ssm/frontend_development_qa.json`은 고정 `NonInteractiveCommands`
Session document다. Action은 Inspect/Setup/Cleanup/AccountProbe로 닫혀 있고 tenant, release ID,
digest는 shell 문자/경로/SSM 참조를 허용하지 않는 strict pattern으로 제한한다.
`SyntheticLongVideo`는 `false`가 기본인 명시적 boolean 문자열이다. `true`인
실행에서만 scenario 명령의 `--synthetic-long-video` 분기를 사용하며 학생을 정확히
2명으로 고정한다. 이 fixture는 READY 영상 metadata 한 건
(`qa-fixtures/video-long/master.m3u8`, 900초)과 두 활성 수강의
`PROCTORED_CLASS` override만 만든다. R2 객체를 만들거나 운영 영상을 복제하지 않는다.
공식 frontend Playwright runner는 API가 반환한 정확한 signed path만 loopback 합성
HLS server에 매핑해야 하며, 이 매핑 없이 metadata 경로를 재생 성공 증거로 사용할 수
없다. 일반 Setup과 기존 caller는 기본 `false`라 영상 fixture가 생기지 않는다.
`SyntheticMultipartVideo`는 별도 raw 영상 검증에서만 `true`로 전달한다. 기본값은
`false`이며 `SyntheticLongVideo` 또는 `AccountProbe`와 함께 사용할 수 없다.
Setup은 격리된 개발 DB와 영상 버킷에서 전용 차시를 확인하고, 실행 시 사용하는 R2
자격으로 해당 차시의 raw prefix에 완료 객체와 미완료 multipart가 없음을 확인한다.
읽기 권한이나 빈 상태를 확인하지 못하면 Setup이 실패한다. 기존 생성 감사 기록에
capability와 차시 ID를 묶어 기록하고 `synthetic_multipart_video`에 `lecture_id`,
`session_id`와 두 빈 목록의 개수를 반환한다. 업로드 뒤의 Inspect는 실제 객체의 바이트
길이, 저장된 영상 상태, AWS Batch 제출 ID가 있는지를 값으로 반환한다.
브라우저를 종료한 뒤 같은 capability로 Cleanup을 호출한다. 서버가 기록한 영상 행의
정확한 원본 키만 대상으로 하며, 미완료 업로드를 중단하고 완료 객체를 지운 뒤 양쪽
목록이 모두 비었음을 확인해야 DB를 정리한다. 행이 이미 삭제된 재호출은 감사 기록의
차시 범위에서 읽기 검증만 한다. 호출자가 임의 키나 업로드 ID를 지정할 수 없다.
일반 Video Batch는 계속 비활성화하며, 이 검증 범위는 원본 객체 조립과 정리까지다.
`NonInteractiveCommands` agent는 command 문자열에 shell을 암묵적으로 추가하지 않는다.
따라서 Linux command는 POSIX tokenization 결과가 정확히
`["/bin/sh", "-lc", <고정 script>]`가 되도록 shell과 단일 script 인자를 명시하며,
script의 첫 줄 `set -eu`는 executable이 아니라 그 shell 안에서 실행한다. 호출자가
command나 shell 인자를 전달하는 parameter는 없고 기존 고정 parameter pattern도
유지한다. 이 argv 경계와 embedded Python 구문은
`python -B -m unittest scripts.v1.test_frontend_development_qa -v`로 검증한다.
개발 settings, 현재 release/image, exact DB/user/R2, mock messaging, billing off,
빈 Video Batch, 개발 큐를 검증한다. Setup은 advisory lock 아래 기존 tenant/user
부재를 확인하며 원래 scenario 명령을 reset 없이 사용한다. 생성과 같은 DB transaction에
`OpsAuditLog(action=development.qa.setup)` 1행으로 exact tenant ID/code와 256-bit
run capability의 SHA-256 digest를 기록한다. 감사 기록 실패도 전체 생성 rollback이다.

계정·첫 수강 흐름도 운영과 같은 fail-closed 알림톡 계약을 사용한다. 개발 환경은
외부 발송이 불가능한 `development-mock-pfid`와 `SOLAPI_MOCK=true`를 함께 고정하고,
scenario Setup은 owner의 학생/학부모 가입 안내 및 비밀번호 복구 4개 설정에 exact
APPROVED mock 템플릿을 보장한다. 이미 유효한 개발 템플릿은 보존한다. 이 공용 mock
템플릿은 개발 런타임의 기준 데이터이므로 QA tenant cleanup 대상이 아니며 수신번호,
자격 증명, 운영 provider 식별자를 저장하지 않는다. 전용 개발 messaging worker가
개발 큐를 소비해 mock provider receipt를 영속화해야 계정 복구 실사용 검증이 통과한다.
빈 개발 DB에서는 같은 transaction이 `OWNER_TENANT_ID`에
`academy-development-owner`/`Academy Development Owner` 활성 tenant를 먼저 만들고
PostgreSQL tenant PK sequence를 전진시킨다. 그 ID나 code가 다른 tenant에 이미
사용됐거나 기존 owner identity가 다르면 데이터를 덮어쓰지 않고 fail-closed한다.

일반 실사용 scenario는 기존 admin 교직원과 별도로 같은 일회용 tenant에
`ymath-qa-organization-owner` owner 계정을 생성한다. 기존 admin을 승격하지 않고
전역 superuser도 부여하지 않는다. 원장 전용 학원 정보 저장→reload→학생 화면 반영을
실제 역할로 검증하기 위한 fixture이며 일반 scenario 비밀번호의 기존 비밀 전달 경계를
사용한다. login UAT의 10/10/10 manifest에는 추가하지 않는다. 이 사용자와 token은
동일 exact tenant destroy/cleanup-zero 대상이며 운영 tenant에는 생성하지 않는다.

Setup 성공 응답의 positive `tenant_id`는 frontend runner가 메모리에만 보관하고,
Cleanup 및 그 직후 Inspect에 고정 문서의 `TenantId`로 그대로 전달한다. 최초 Inspect와
Setup은 기본값 `0`을 사용한다. 고정 Setup은 DB 생성 직후 확정된 positive tenant PK를
post-commit readback보다 먼저 실패 context에 기록하므로, 그 이후 검증이 실패해도 안전
payload가 정확한 cleanup target을 반환한다. Cleanup은 같은 advisory lock/transaction 아래 요청
`TenantId`와 exact tenant PK가 같고, 그 tenant에 연결된 성공 소유권 행이 정확히 1개이며
요청 capability digest가 일치할 때만 진행한다. 누락·중복·다른 run capability·다른
tenant ID는 거부한다. 이미 DB에서 부재하면 전달받은 positive `TenantId`의 R2 prefix까지
직접 0인지 읽고 destroy는 호출하지 않는다. 생성/정리가 겹쳐도 소유권 검사와 destroy
사이에 lock을 풀지 않는다. destroy는 user 삭제 전에 exact scenario user의
SimpleJWT outstanding token만 삭제한다. 학생 activity 감사 행은
`student_activity.login/screen_view/target_open`, exact target tenant, scenario
actor/target user, 현재 시각 이하, 그리고 최초 성공 `development.qa.setup` seal 시각
이후를 모두 만족할 때만 삭제한다. 일반 command 실행은 생성 transaction에 중복 없이
남긴 `development.qa.scenario` provenance seal을 같은 방식의 하한으로 사용하고, 고정
SSM 실행에서는 더 좁은 `development.qa.setup` seal을 우선한다. activity가 있는데 둘 다
없으면 시간 범위를 추정하지 않고 전체 destroy를 거부한다. 다른 tenant/user, 다른 actor,
선택된 seal 이전 감사 행은 보존한다. 두 PII-free provenance seal 자체도 tenant 삭제 후
FK가 NULL인 보안 증거로 남는다.

급여 실사용 후의 destroy와 reset은 같은 transaction 안에서 exact scenario의
WorkRecord를 먼저 삭제한 뒤 tenant cascade를 수행한다. WorkType의 제품용
`PROTECT`는 유지한다. 소유 Staff·WorkType과 급여 자식 행을 잠그고 WorkRecord,
StaffWorkType, ExpenseRecord, WorkMonthLock, PayrollSnapshot 각각의 tenant와
staff/work_type 관계의 tenant가 모두 일치하는지 확인한다. 다른 tenant에서 이
scenario의 Staff/WorkType을 참조하는 행도 토큰·활동 감사 삭제 전에 거부한다.
다른 tenant의 기록을 따라가서
지우지 않는다. 삭제 전 `work_records` 수를 counts에 남긴다. 잠근 exact ID 목록의
건수와 실제 선삭제 수가 다르면 실패한다. 후행 tenant 삭제나 reset 재생성이
실패하면 급여 기록·토큰·활동 감사의
선삭제도 함께 rollback하며 성공 응답으로 바꾸지 않는다. 일반 tenant 삭제나
운영 과거 급여 데이터에는 이 scenario 전용 순서를 적용하지 않는다.
`test_setup_ymath_realuse_scenario.py`의 `payroll_cleanup` 회귀는 실제 급여 모델
그래프의 destroy/reset, 양방향 cross-tenant 거부, 후행 실패 rollback 및 foreign
tenant 보존을 검증한다. SQLite 검증은 PostgreSQL의 실제 행 잠금·동시성 증거가
아니므로 기존 격리 PostgreSQL command/lock CI를 함께 유지한다.

Inspect/Setup/Cleanup 출력은 tenant/user 수와 별도로 `outstanding_tokens`,
`activity_audits`, exact development R2 prefix의 `r2_objects`, exact `QA_TENANT`
환경을 가진 다른 container process의 `processes`, container port 18000 LISTEN의
`listeners`를 모두 numeric residue로 반환한다. Setup/Cleanup에서 하나라도 0이 아니면
고정 문서가 실패한다. Cleanup은 capability와 `TenantId` 검증을 마친 뒤 DB destroy보다
먼저 exact R2 prefix의 object만 삭제하고, 각 prefix를 다시 열거해 strong-consistency
0을 확인한다. R2 실패 시 DB tenant와 소유권 seal은 보존되어 같은 exact target으로
안전하게 재시도할 수 있다. R2는 `tenants/<id>/`, `excel/<id>/`,
`tenant-logos/<id>/`, `landing-public/reviews/<id>/`,
`matchup-showcase-snapshots/tenant_<id>/`, 급여·출결 엑셀의 `exports/<id>/`만 열거·삭제하며 broad prefix나 다른 tenant
object는 건드리지 않는다. process/listener 수는 원격 development API container 경계다. runner 로컬
tunnel/process와 AWS Session tuple은 frontend 계약이 별도로 종료·증명한다. 이 변경은
스키마나 기존 데이터 migration을 만들지 않는다.

일반 FE QA 학원을 삭제하거나 재설정할 때, 공용 발신 학원에 저장된 클리닉 자동 예약·취소
모의 발송 기록도 회수한다. 학원이 존재하는 동안 Setup 소유권, 공용 발신 학원, 모의 공급사
접수 번호를 검증하고 원학원의 발송 대기열과 업무 키·수신자 지문·대상·원천을 대조한다.
검증한 행만 삭제한 뒤 학원 삭제 후에도 같은 PK로 잔여를 확인한다. 삭제한 수는 일반 정리
결과의 `deleted.clinic_mock_receipts`에 기록한다. 소유권이나 수신 증거가 맞지 않으면
트랜잭션을 되돌려 같은 학원에서 재시도할 수 있다. 다른 학원, 수동 발송, 원학원 식별자가
이미 비어 있는 과거 기록은 보존한다.

기존 QA tenant를 Inspect할 때도 tenant-scoped 감사·토큰 residue 조회는 하나의
`transaction.atomic()` 안에서 실행한다. 따라서 PostgreSQL의 transaction-local tenant
context가 조회 도중 풀리지 않으며, 외부 R2/process/listener readback은 DB transaction을
끝낸 뒤 계속 수행한다.

Cleanup과 positive `TenantId`를 받은 사후 Inspect의 성공·실패 출력은 요청 target
`tenant_id`를 그대로 echo한다. 사후 Inspect는 DB tenant가 이미 없어도 그 ID의 exact R2
prefix를 실제 열거하고 `r2_scope_proven=true`를 반환한다. `TenantId=0`인 최초 absent
Inspect는 R2 target을 추측하지 않고 `r2_scope_proven=false`로 구분한다. 실패 출력은
allowlist된 `failure_stage`, exception type, request `tenant_id`, 다섯 numeric residue만
포함하며 provider 오류 문자열, capability, 사용자 식별자, secret은 출력하지 않는다.

영상 분기를 켠 Setup은 별도 `video_state`에 `videos=1`,
`video_accesses=2`, `proctored_video_accesses=2`를 반환하고 progress/session/event와
PLAYER_ERROR/위반 수가 모두 0임을 검증한다. Inspect의 `video_state`는 soft-delete를
포함한 tenant 전체의 같은 키를 현재 숫자로 반환한다. 다른 실사용 흐름이 만든 뒤
soft-delete한 학습 영상도 여기에 포함되므로 단일 장시간 영상의 성공 수치로 쓰지 않는다.
장시간 재생 뒤 Inspect만 Setup에서 반환한 exact positive `TenantId`와 `VideoId`를
함께 보낸다. `VideoId`는 기본 `0`인 정수 문자열이며 양수는 `Inspect`와
`SyntheticLongVideo=true`, positive `TenantId` 조합에서만 허용한다. 서버는 해당
tenant code/PK, 영상 PK, 비삭제 상태 및 고정 synthetic metadata identity를 검증한 뒤
그 영상만의 `synthetic_video_state`와 검증된 `synthetic_video_id`를 별도로 반환한다.
해당 ID가 없거나 다른 tenant, soft-delete된 대상, 다른 영상 identity면 실패한다.
이 조회는 기존 Inspect의 DB transaction 안에서 실행하며, tenant 전체 집계나
creation/cleanup capability 권한을 대체하지 않는다. frontend는 Setup ID echo를 확인하고
기존 영상 1/권한 2/진도 2/session 4/active·error·violation 0 조건을 그대로 적용한다.
숫자 관측은 frontend가 assertion 전에 allowlist로 보존하며 원문 응답이나 비밀값은 남기지 않는다.

Cleanup과 이미 부재한 cleanup은 계속 soft-delete 포함 tenant 전체 영상, 권한, 진도,
재생 session/event, 활성 session, PLAYER_ERROR, 위반 event가 전부 0인
`video_residue`를 요구한다. 이름·전화·로그인 ID 같은 사용자 값은 이 readback에
포함하지 않는다.
회귀는 기존 scenario command DB 테스트의 두 학생 정상 재생+잔존 학습 영상 혼재,
잘못된/다른 tenant/삭제 대상 거부와 전체 cleanup 0, 고정 문서의 offline Inspect
control-flow 테스트가 소유한다. 합성 입력 회귀는 과거 실패 실행의 누락된 runtime
payload를 복구한 증거가 아니며 다른 수치 불일치 가능성도 배제하지 않는다.
이 계약의 적용에는 새 command가 들어간 API candidate의 공식 배포와 검토된 고정 SSM
문서 버전의 적용·readback이 모두 필요하다. 문서 파일/로컬 테스트 통과만으로 live 적용이나
공식 frontend gate 성공을 주장하지 않는다. IAM grant·신뢰·포트·timeout 변경은 없다.

이 경계는 생성 요청자가 보유한 capability의 증명이며 GitHub JWT의 run claim을 서버가
직접 검증한 것은 아니다. 원문 capability는 runner 메모리와 고정 SSM parameter로만
전달하고 artifact/CLI stdout/오류에 넣지 않는다. SSM control-plane 요청 기록이나 host
관리 권한은 별도 신뢰 경계이며 그런 기록에 대한 접근 권한을 frontend role에 추가하지
않는다. capability를 잃거나 기존 소유권 기록이 없으면 자동 재발급·채택·타 run cleanup을
하지 않고 별도 exact-target 복구 검토를 요구한다. 운영 행이나 비밀값을 복제하지
않는다. Port document는 remote8000/local18000으로 고정하며 host/shell 입력이 없다.

SSM Command API의 `GetCommandInvocation`은 resource-level 제한을 지원하지 않으므로
개발 명령 출력에만 한정된 권한이라고 주장할 수 없다. 새 역할은 SendCommand,
GetCommandInvocation/ListCommandInvocations/ListCommands를 명시적으로 거부하고
자기 Session의 결과만 받는다. StartSession은 두 exact document와 active-development
태그 인스턴스에 제한한다. 저장소 정책의 `StartFixedDocuments`는 두 승인 문서에
`BoolIfExists: {ssm:SessionDocumentAccessCheck: true}`를 사용한다. 승인 문서의
missing/true는 허용하고 명시적 false는 거부한다. 조건 전체를 삭제하거나 승인 문서를
추가하지 않는다. 인스턴스 statement는
기존 account/region ARN과 Name/ManagedBy/Environment/Lifecycle 네 태그를 그대로
검사한다. TerminateSession은 session의 caller tag와 aws:userid가 일치해야 한다.

instance의 strict Bool을 문서로 옮긴 것만으로는 실제 연결이 해결되지 않았으며,
공식 개발 run은 고정 문서의 `StartSession` AccessDenied에서도 중단됐다. 이 변경은
문서 statement의 연산자 한 키만 바꾼 저장소 계약이며 실제 해결 증거가 아니다.
CloudTrail은 실제 평가 key의 missing/false를 공개하지 않으므로 그 값을 추정하지 않는다.
저장소 테스트/PR/CI 성공은 live IAM 적용이나 SSM runtime red→green 증거가 아니다.
기존 역할이 있으면 planner는 같은 이름의 inline 정책 정확히 1개와 attached 정책 0개를
읽어 확인한다. 누락·중복·다른 inline·attached grant는 거부하고 정상 inventory여도
기존 역할의 자동 갱신 없이 별도 검토를 요구하며 중단한다. 별도 exact-main/정책 hash/
공용 잠금/승인 경계의 단발 적용과 readback 전에는 기존 live 정책을 수정하지 않는다.

planner의 45개 사례는 각 요청 context를 명시한다. 두 승인 문서와 기본 셸·승인외
문서·RemoteHost/일반 Port/Interactive/SSH의 exact ARN에 missing/false/true를 각각
평가한다. 승인 문서의 missing 기대값만 allow로 바뀌며 false 거부와 승인외 문서의
grant 부재는 유지한다. Amazon 소유 public document는 account 부분이 빈 ARN을 사용한다.
인스턴스 허용 사례에는 document key가 없으며 네 태그의 개별 불일치도 검사한다.
나머지 정책 key의 합성 positive controls는 서비스에서 관측한 값이 아니다. 생략한
document key를 공통값으로 다시 채우지 않는다. simulator가 보고한 missing key는
해당 사례가 의도적으로 생략한 document key만 허용하며, 다른 누락·이미 제공한 key의
누락 보고·기대와 다른 결정은 전체 계획 실패다. 로컬 fake는 요청 matrix와 판정 집계만
검사하며 IAM 판정기를 흉내 내거나 실제 세션 성공을 주장하지 않는다.

[AWS 공식 정책 예제](https://aws.amazon.com/blogs/security/how-to-enable-secure-seamless-single-sign-on-to-amazon-ec2-windows-instances-with-aws-sso/)는
StartSession에 `BoolIfExists`를 사용한다. 이는 Windows Fleet Manager 사례로,
이 저장소의 문서-only statement 배치가 검증됐다는 뜻은 아니다.
[현재 Quickstart](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-restrict-access-quickstart.html)의
node/document allowlist 예제에는 이 조건이 없으므로 필수 연산자라고 주장하지 않는다.
[IAM IfExists 규칙](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_elements_condition_operators.html#Conditions_IfExists)에
따라 missing은 허용하되 false 거부를 유지하는 최소 변경이다.
[기본 문서 계약](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-default-session-document.html)은
DocumentName 생략 시 기본 셸 문서의 IAM 권한도 요구하며,
[사용자 지정 문서 계약](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-specify-session-document.html)은
지정 문서의 권한이 없으면 요청이 실패한다고 명시한다. 네 태그의 node grant만으로
문서 권한이 생기는 것은 아니다. exact2docs 밖의 grant가 없는 현재 inventory에서는
별도 shell Deny를 추가하지 않는다. 기존 Run Command Deny는 StartSession 거부 증거가 아니다.
ARN별 policy 검증은 생략 요청의 서비스 측 문서 선택이나 실제 shell 거부를 증명하지
않는다. 기본/foreign 문서의 StartSession 실호출은 이 릴리스 검증에 포함하지 않으며,
그 미실행을 공식 same-artifact flow의 추가 차단 조건으로 삼지 않는다. 실제 진행은
별도 승인 후 검토된 fixed Inspect/Setup/Cleanup과 공식 same-artifact QA로 제한한다.
실패하면 권한 확대나 guard 우회 없이 중단한다. 정책 연산자 변경은 기존 tenant/user/
소유권 데이터, trust, 문서의 명령·parameter·포트·수명 설정을 변경하지 않는다.

`ssmmessages:OpenDataChannel`도 resource-level ARN을 지원하지 않으므로 이 action만
Resource:*가 필요하다. 채널 인증은 StartSession의 session/caller 정보를 담은
TokenValue에 의존한다. 이를 IAM-level foreign-channel denial이나 실제 세션 성공
증거로 표현하지 않는다. TokenValue는 출력/공유하지 않는다. identity-policy simulation과
실제 OIDC/세션/복호화 검증을 구분하고, 검토된 변경의 실제 적용 전에는 synthetic QA를
계속 HOLD한다. 관련 AWS 정본은
[StartSession](https://docs.aws.amazon.com/systems-manager/latest/APIReference/API_StartSession.html)과
[Session schema](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-schema.html)다.

frontend runner/workflow와 원본 응답·CORS 보존, exact artifact, non-skipped 필수 실사용,
cleanup0 승격 계약은 frontend `docs/DEPLOYMENT-OPERATIONS.md`가 소유한다.

#### 세션 제한과 장애 경계

고정 QA document의 `inputs`는 maxSessionDuration=5분/idleSessionTimeout=5분,
Port document는 100분/5분이다. frontend의 95분 실사용 검사와 100분 로컬 tunnel
제한에 맞춰, 연결 시작·검사 종료에 5분 여유를 둔다. 호출자가 timeout을 바꾸는
parameter는 없다. 공식 실행35543367845의 기존25분 연결은23:07:01Z부터
23:32:01Z까지 정확히25분 뒤 종료되어, 진행 중이던 자료함 삭제가 ECONNREFUSED로
실패했다. 브라우저 timeout만 늘려서는 이 서버 측 수명을 바꿀 수 없다.
이후 실사용 검사 예산이 50분으로 늘었지만 서버 문서의 35분 제한은 남아 있었다.
실행37372206862의 연결도 2026-10-05 21:25:23Z부터 22:00:23Z까지 정확히
35분 뒤 종료돼 당시 55분으로 맞췄다. 이후 실행38843507538에서 36건이
39분24초에 통과한 뒤 마지막 영상 검증 도중 50분 검사 프로세스 한도에 도달했다.
당시 영상 검증의 개별 17분 한도를 포함하도록 검사 60분·터널 65분으로 맞췄다.
당시 양쪽 QA tenant/user/R2/process/listener 정리 0은 확인됐고 운영 승격은 차단됐다.
이후 실행40168553418은 기존 36건에 47.79분이 걸려 60분 전체 한도에서 마지막 영상이
중단됐다. 이후 실행45431752643은 41건을 71.513분에 통과한 뒤 마지막 영상의
개별 17분 한도보다 짧은 잔여 예산 때문에 85분 부모 deadline에서 SIGTERM됐다.
당시 primary/cross tenant Cleanup과 독립 Inspect는 모두 0이고 운영 승격은 차단됐다.
현재 42건의 검사 예산은 95분, 서버·로컬 터널은 100분으로 맞춘다.
개별 테스트 제한과 실제 690초 재생·갱신·동시 사용자 검증, 단일 worker·재시도 0·
전체 필수 통과·cleanup0 조건은 유지한다. 고정 8000→18000 포트,
5분 idle 제한, 역할·tenant/data 범위와 QA command 5분 수명은 바꾸지 않는다.
영상 갱신 중 먼저 발생한 worker timeout은 이 수명 차이와 별도로 진단한다.
QA CI job은 설치·정리·증거 업로드를 포함해 120분이며, 해당 job의 OIDC 요청과
`academy-frontend-development-qa` 역할의 MaxSessionDuration은 7200초(120분)로 맞춘다.
기존 3600초 인증이 검사 종료 전후 만료돼 Cleanup/Inspect를 막는 일을 방지한다.
95분 검사·100분 터널 이후에도 준비·정리·증거 업로드 여유를 보존한다.
별도 readiness job은 기존 3600초 요청을 유지한다. 역할의 trust·inline policy·
action·resource와 QA command의 5분 수명은 바꾸지 않는다.
이 역할 수명의 선언적 Apply 구현은 현재 저장소에 없다.
`converge_frontend_development_qa.py --frontend-role-plan --aws-profile <profile>`는
문서·정책 검토 계획이며 frontend IAM/SSM을 적용하지 않는다. 성공한 manifest를 가진
정확한 보호 main 이후 배포 담당이 기존 수동 운영 경계에서 해당 역할의
MaxSessionDuration만 7200으로 갱신하고, 검토한 Port 문서의 새 버전을 default로 지정한다.
소스 변경이나 frontend CI 요청만으로 실제 IAM/SSM 설정이 수렴했다고 간주하지 않는다.
역할 수명과 Port 문서 변경은 깨끗한 최신 main·성공 manifest ancestry·공유 배포 lock·
활성 개발 세션 0을 확인한 뒤 exact 기존 설정을 대조하고 적용한다. 변경 전 버전과
정책 hash를 보존하며, default/latest 문서·역할 수명·불변 trust/policy의 readback과
새 세션의 전체 실사용·정리 검증 전에는 적용 완료로 보고하지 않는다. IAM simulation은
이 수명이나 데이터 소유권을 검증하지 않으며, STS 만료는 기존 SSM 세션 종료 보장이 아니다.

QA 원격 명령은 curl 각 10초, Docker inspect 15초(+kill 5초), Docker exec 210초
(+kill 5초), 내부 Python alarm 180초로 제한한다. DB transaction의 statement timeout은
150초/lock timeout은 5초이며 SSM secret read는 connect/read 각 10초, 최대 2회다.
외부 timeout이 Docker client를 종료하는 것과 내부 Python/DB가 중단되는 것은 별개이므로
내부 deadline과 DB timeout을 함께 둔다. 원격 host/프로세스의 비정상 정지까지 데이터
정리를 보장하는 장치는 아니다. 세션 최대 수명이 끝나도 tenant는 자동 삭제되지 않는다.

고정 문서의 소유권 함수 및 실제 `run()` cleanup 분기는
`scripts/v1/test_frontend_development_qa.py`에서 로컬 fake DB/command로 검사한다.
타 run/미소유/중복 record의 destroy 호출은 0, 자기 소유만 1, 부재는 0을 요구한다.
또한 token/activity/R2/process/listener/AIJob/AIResult/AI Redis residue가 모두 numeric 0이어야 한다. 실제
cleanup 명령 회귀는 scenario command 테스트에서 소유 user token과 seal 이후 activity만
삭제하고, 타 tenant token·다른 actor·seal 이전 activity·setup seal을 보존하는지
검증한다. 외부 QA tenant나 실제 DB 행을 삭제하는 회귀가 아니다.

고정 QA의 AI 정리는 `apps/core/services/development_qa_ai_cleanup.py`가 소유한다.
Redis는 개발 host-local `127.0.0.1:6379`만 허용하며 외부 endpoint나 미설정은 거부한다.
정확한 `qa-ymath-realuse-fe-*` tenant code/ID와 생성 capability seal을 검증하고,
seal 이후 생성된 최대 1,000개의 정확한 job ID/사전 건수를 확인한다. DONE/FAILED/
REJECTED_BAD_INPUT/REVIEW_REQUIRED이면서 완료 시각이 있고 lease/lock이 없는 작업만
허용한다. 실행 중 작업, payload tenant 불일치, seal 이전 작업, 출처 불명 cache,
중복 seal은 snapshot/R2/tenant 삭제 전에 거부한다. R2 정리 이후에도 같은 inventory를
재검사한다. 해당 job의 tenant status/progress와 legacy status/progress 네 키만 삭제하며,
정확한 AIJob 삭제에 연결된 AIResult만 cascade한다. 일반 사용자 데이터 보존 기간이나
제품의 AI 결과 보존 정책은 바꾸지 않는다.

영상 멀티파트 격리 검사는 setup seal에 보관한 정확한 session ID까지 capability
digest에 포함한다. AI Inspect와 Cleanup도 이 seal과 원래 capability를 검증하고
같은 소유권을 정리 receipt에 보존한다. 기존 일반 검사의 seal과 receipt는 같은
방식으로 계속 읽으며, 잘못된 session이나 capability는 정리 전에 거부한다.
계정 스냅샷 정리도 같은 setup seal 검증을 사용한다. 멀티파트 Cleanup에서는
스냅샷이 없어야 한다. 계정 스냅샷 생성·비교는 일반 격리 계정 검사에서만 실행한다.

`development.qa.ai.cleanup` 감사 receipt에는 기존 owner hash, 정확한 job ID와 사전
AIJob/AIResult/Redis 건수만 저장한다. setup seal과 receipt는 tenant 삭제 후 SET_NULL로
보존한다. 별도 Inspect는 원래 capability로 seal을 다시 검증하고 이 ID들의 네 키 및
정확한 tenant cache namespace를 읽어 `ai_jobs/ai_results/ai_redis_keys`와
`ai_scope_proven=true`를 반환한다. Redis 연결 실패·불완전 inventory·잔여 건수는 0으로
간주하지 않는다. DB rollback 후 Redis 삭제는 멱등 재검사가 가능하며, 뒤늦은 cache 쓰기나
job 생성은 후속 Inspect 실패로 남긴다. AI 내용/결과 payload/비밀값은 출력하지 않는다.

이미 삭제된 과거 QA에 receipt가 없거나 capability 원문을 잃었다면 일반 Cleanup/Inspect로
소유권을 채택하지 않는다. 과거 tenant ID, capability hash와 release/digest 증거만으로
원문 권한을 복원할 수 없으며 별도의 정확 대상 복구 검토가 필요하다. 이 변경은 기존
잔여를 자동 삭제하지 않는다. 회귀는 `apps/core/tests/test_development_qa_ai_cleanup.py`
및 fixed SSM offline 계약으로 검증한다. 후보 API image에 helper를 포함한 뒤 그 후보와
고정 SSM 문서를 함께 수렴시켜야 하며, frontend cleanup gate도 세 AI 건수를 요구한다.

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

`Assert-AwsMutationIdentity`는 명시적으로 배정된 수동 작업에서 기존 계정 루트
인증을 경고와 함께 허용한다. 이를 배포의 전역 금지나 게이트 우회로 해석하지 않는다.
CI와 일반 자동 배포는 `academy-gha-ecr-build` GitHub OIDC 역할을 사용하며 장기
AWS access key를 요구하지 않는다. 수동 작업도 최신 clean main, 공유 잠금과 검증을 유지한다.
개발 권한은 기존 운영 inline 정책과 분리된 고객 관리형
`academy-gha-development-deploy` 정책으로 관리하며
`converge-api-development-oidc.ps1`이 backend main-ref와 승인된 production
environment 두 subject만 허용하는 trust와 정책 readback을 강제한다. 환경 없는
development job은 main-ref subject를 사용하고, production environment가 붙은
lock/mutation job은 environment subject를 사용한다.
역할에 연결된 관리형 정책은 development와 별도 운영 전환 정책
`academy-gha-account-cutover`, `academy-gha-openai-runtime`까지 세 개로 정확히 검증한다.
OpenAI source/게시 권한은 [공급자 전환 계약](openai-provider.md)이 소유하며 운영 전환
권한을 개발 정책에 섞지 않는다. `Ensure-GitHubActionsDeployIAM`이 세 정책을 소유 순서대로 수렴한 뒤
전체 inventory를 readback하며, 개발 정책만 수렴하는 스크립트는 운영 정책을 변경하지 않는다.

## 릴리스 순서

백엔드 릴리스는 아래 순서를 건너뛸 수 없다.

1. GitHub Actions가 run-unique 태그로 immutable 후보 이미지를 빌드한다.
2. `publish-api-development-env.ps1`이 운영 형태의 값을 복사하되 DB·큐·R2·발송/결제를
   개발 경계로 치환한다. 메시징은 dry-run 조기 종료를 쓰지 않고 durable outbox와
   전용 개발 SQS를 통과하며, API와 Messaging worker의 `SOLAPI_MOCK=true`가 외부
   공급자 호출과 비용을 차단한다. API의 외부 AI 자격증명은 제거한다. 개발 worker는
   사용자가 승인한 기존 `OPENAI_API_KEY`를 개발 전용
   `/academy/providers/development/openai-api-key`에서 읽어 재사용하며 새 프로젝트·결제·별도
   개발 키를 필수로 요구하지 않는다. 다른 외부 AI·정적 AWS 자격증명은 계속 제거한다.
   provider 호출은 고정한 합성/승인 fixture와 일회용 QA tenant에 한정하고 기존 통과
   증거를 재사용한다. 혼합 7쪽 PDF의 전체 쪽 PPT와 직접 자르기 경로는 OpenAI 호출이
   없으며, Matchup만 필요한 최소 fixture 호출을 수행한다. 키 값은 로그·Git·보고서에
   남기지 않는다. DB·R2·큐·Redis 격리와 정확한 생성물 정리 검증은 그대로 필수다.
   이 개발 worker 예외는 preprod에 적용하지 않으며 preprod는 OpenAI와 폐기된 Gemini 키를 모두 제거한다.
   채점된 OMR 학생 정정의 격리 실검증은 이 publisher의
   `-EnableOmrScoredReassignment`를 명시해 API와 worker 개발 복사본에만
   `OMR_SCORED_REASSIGNMENT_ENABLED=true`를 넣는다. 옵션이 없으면 기존 원본
   값/기본값 전달 동작을 유지하며 운영 SSM 값을 수정하지 않는다. 새 secret,
   운영 자원 접근, IAM 권한은 추가하지 않는다. 두 parameter의 정확한 버전
   readback에서 flag도 대조하고, 반환된 `parameter_version`과
   `workers_parameter_version`을 기존 배포기의 각각의 expected version으로
   전달한다. 게시만으로 기존 컨테이너 환경이 갱신되지는 않는다.
   정식 빌드/배포 workflow의 `verify-api-development`는 이 옵션을 명시한다.
   따라서 동일 candidate의 API·AI·Tools가 개발 환경에서 활성 상태로 검증되며,
   운영 환경과 옵션 없는 수동 게시의 기존 기본 동작은 유지한다. 개발 blue/green
   교체의 새 컨테이너 기동 및 이전 개발 인스턴스 종료를 확인한 뒤에만 실제 OMR
   합성 fixture를 쓴다. 별도 수동 개발 rollout으로 이 순서를 중복 실행하지 않는다.
   실행 전 API/AI의 manifest digest·release/source·개발 DB/role·실제 Django
   flag=true와 API 8000 listener 소유 컨테이너를 확인한다. SSM version은
   publisher readback과 고정된 bootstrap으로 증명하며 컨테이너에서 직접
   관측했다고 표기하지 않는다. 검증 후 동일 컨테이너/이미지와 합성 tenant
   cleanup zero를 재확인한다. HTTP timeout으로 서버 작업 종료가 불명확하면
   임의 삭제나 zero 판정을 하지 않고 정확한 소유 scope를 복구 담당에게 남긴다.
   운영 활성화의 호환성/이력 보존 조건은 [OMR 소유 문서](../domain/omr.md#채점된-종이의-학생-귀속-정정)를 따른다.
3. `deploy-api-development.ps1`이 새 candidate 인스턴스를 만들고 migration, DB 역할,
   운영 DB 접근 거부, 개발 큐, 개발 R2와 운영 R2 접근 거부, Redis, `/healthz`,
   `/health`, 정확한 API/Tools/AI digest를 검증한다.
4. `run-api-development-smoke.ps1`이 합성 학생 XLSX 파싱, 혼합 PDF의 4장 반전 PPTX 생성·재열기,
   API와 AI worker 설정 경로 각각에서 개발 R2 객체 put/get/delete를 실행하고
   각 처리시간을 기록한다.
   현재 이 smoke는 Video Batch 제출 성공을 증명하지 않는다. Video Batch는 전용 개발
   queue/job definition/job role과 Batch에서 접근 가능한 개발 Redis가 준비되기 전까지
   비활성 상태여야 한다.
5. 모든 검증이 성공한 뒤에만 candidate를 active로 승격하고 이전 개발 인스턴스를
   종료한다. 실패하면 candidate만 종료하고 기존 active 인스턴스를 보존한다.
6. 오답노트 출력·문항 분리 관련 변경 또는 전체 재빌드 때는 이전 개발 인스턴스의
   종료 뒤 같은 배포 lock 안에서 아래 오답노트 실사용 게이트까지 통과해야 한다.
   이후 별도 임시 preprod EC2 게이트가 통과해야 한다.
7. 그 뒤에만 운영 migration과 ASG/ALB 무중단 교체를 허용한다.

### 수강 등록 알림톡의 개발 실사용 검증

정규 배포 담당자는 이전 개발 호스트 종료 후 공유 lock을 보유한 상태에서 다음 고정 명령을 사용한다. 입력은 해당 실행의 후보 manifest, release ID, 운영 DB 이름 및 lock owner다.

```powershell
./scripts/v1/run-wrong-note-development-smoke.ps1 -EnrollmentNotices -ManifestPath $manifestPath -ReleaseId $releaseId -ProductionDatabase $productionDatabase -LockOwner $lockOwner
```

기본 명령은 학생만 발송하는 경우와 학부모만 발송하는 경우를 차례로 실행한다. 각 실행은 새 일회용 학원에 학생 두 명과 각 학생의 학부모 계정을 연결하고, 시작과 종료 시 API·Messaging 이미지와 호스트의 verified release를 확인한다. 강사는 같은 등록 요청을 두 번 제출한다. 개발 API 이미지의 별도 프로세스가 해당 등록만 주기적으로 처리한다. 운영의 분당 EventBridge 경로는 `infra/terraform/messaging_schedule.tf`에서 관리한다.

담당자는 동일 후보의 `selected_role=student`와 `selected_role=parent` 성공 결과를 각각 확인한다. 한 역할의 검사가 중단되면 남아 있는 소유 범위를 먼저 정리하고, 같은 후보에서 `-EnrollmentRecipient student` 또는 `-EnrollmentRecipient parent`로 해당 역할을 다시 검증할 수 있다.

각 실행에서 선택한 역할의 수신자 두 명만 mock으로 발송한다. 결과 API의 로그 ID 두 개가 실제 mock 호출·큐 삭제 영수증과 일치해야 하며, 제외한 두 명은 발송 예약과 로그가 모두 없어야 한다. 첫 번째 가족의 메시지는 공급사 결과 저장 후 한 번 재수신시켜 같은 업무 키와 SQS 메시지 ID로 기존 결과를 재사용하는지 확인한다. 양쪽 실행의 결과에 선택·제외한 역할과 개별 정리 결과를 기록한다. 단말 도착 여부는 이 검사에서 확인할 수 없다.

정리는 setup 소유권, 발신 owner 및 원래 학원, 수신자·등록·업무 키, 메시지·로그 PK를 확인한다. 소유한 임시 로그·진행 캐시와 합성 학원을 정리하고 트랜잭션 커밋 후 다시 조회한다. 다른 학원의 발송 기록과 개발 공용 owner는 보존한다. 작업이 진행 중이거나 소유권이 다르면 복구 파일을 유지한다. 같은 호스트와 release에서 작업 상태를 확인한 뒤 위 명령에 `-CleanupOnly`를 추가해 소유 범위만 다시 정리한다. 검사 결과에는 전화번호, 메시지 본문, 인증정보가 들어가지 않는다.

### 클리닉 마감 후 예약 알림톡의 개발 실사용 검증

정규 배포 담당자가 이전 호스트 종료 후 공유 lock을 보유한 상태에서 해당 후보의 API와 Messaging 워커로 실행한다.

```powershell
./scripts/v1/run-wrong-note-development-smoke.ps1 -ClinicNotices -ManifestPath $manifestPath -ReleaseId $releaseId -ProductionDatabase $productionDatabase -LockOwner $lockOwner
```

합성 학생이 마감된 회차에 신청하면 거절된다. 관리자가 마감 후 신청을 켜면 같은 학생의 신청이 대기 명단에 들어간다. 학생과 관리자가 저장된 상태를 각각 다시 조회한다. 워커는 신청 시 학생·학부모·지정 교직원 세 명에게, 승인 시 학생·학부모 두 명에게 승인 양식과 해당 상태의 선생님메모로 발송한다. 같은 신청과 승인을 한 번 더 제출해도 발송이 늘지 않아야 한다. 외부 공급사 경계에는 개발 모의를 사용한다.

결과 `CLINIC_NOTICE_REAL_USE_PASS`의 다섯 행은 신청·승인 구분, 수신 역할, 원학원 outbox와 공용 발신 학원 로그, 모의 공급사 결과, SQS 메시지 및 ACK 기록을 연결한다. 담당자 사본은 결과 저장 후 가시성 시간을 줄여 실제 큐에서 한 번 더 수신한다. 같은 업무 키와 메시지 ID로 저장된 결과를 재사용하고, SQS 삭제 성공 뒤에만 ACK를 기록한다.

정리는 setup 소유권과 정확한 행 ID를 대조하고 커밋 후 원학원의 계정·회차·예약·outbox, 공용 발신 학원의 해당 로그, 워커 기록과 진행 캐시의 잔여를 다시 조회한다. 다른 학원의 기록은 유지한다. 쓰기 결과가 불확실하거나 큐 처리가 끝나지 않았으면 복구 정보를 보존한다. 같은 release와 호스트에서 위 명령에 `-CleanupOnly`를 더해 정리를 재시도할 수 있다.

### 차시 범위 오답노트의 개발 실사용 검증

`verify-api-development`의 학생별 PDF 검사는 마지막 성공 manifest부터 관련 파일이
달라진 경우, 수동 전체 빌드 또는 비교할 수 없는 기준 때문에 전체 빌드하는 경우에 실행한다.
검사 전후에 공유 lock, 유일한 관리 대상 개발 호스트, active·verified release 태그, API·AI·Tools
실행 digest, loopback 8000 리스너 소유권을 대조한다. 교체 중인 두 호스트가 같은 개발
큐를 읽으므로 기존 인스턴스 종료 전에는 이 검사를 실행하지 않는다.

`run-wrong-note-development-smoke.ps1`은 API 컨테이너의 `verify_development_wrong_notes`를
호출한다. 정확한 release와 개발 DB/role, 운영 DB 접속 거부, 개발 전용 큐·버킷과 호스트 Redis가
일치해야 합성 계정을 만든다. 교직원 로그인 → 합성 시험 25문항·워크북 5문항 자료 업로드 →
실제 AI 소비 → 문항·정답 승인 및 재조회 → 정규 1~2차시 학생 두 명 묶음 요청을 실행한다.
같은 요청의 배치 재사용, 학생별 Tools 작업 완료, 실제 서명 주소로 받은 ZIP의 학생명·개별
문제 이미지/번호·정답을 대조한다. `WRONG_NOTE_REAL_USE_PASS`에는 문항·출력 건수와
정리 건수가 들어간다. 개발 AI·Tools 소비자는 SQS DeleteMessage 성공 뒤 소유한 작업의
setup 감사 ID, release, job ID·종류, 큐, 워커 종류, SQS MessageId를 기록한다.
정규 검사와 프런트엔드 고정 Cleanup 모두 문항 분리·오답 PDF 시나리오의 작업 및 후속
매치업 색인 작업에 대해 이 기록과 DB 종료·lease 해제를 대조한다. 정확한 소비 증거가
없으면 정리를 멈추며 개발 큐 전체를 조회하거나 비우지 않는다.
프런트엔드 고정 Inspect는 이 시나리오의 테넌트가 살아 있으면 DB lock 밖에서 문항
분리·색인·출력 작업 종료를 최대 100초 기다린다. Cleanup은 소유권, 종료 상태와 ACK를 다시 확인한다.
DeleteMessage 이후 DB 연결 오류가 발생하면 소비자는 같은 MessageId의 감사 저장을 최대 세 번
시도한다. 모두 실패하면 원래 컨테이너의 `AI_JOB_SQS_ACK`와 `DEVELOPMENT_QA_ACK_RECORD_FAILED`를
정확한 release·작업·큐와 함께 보존한다. 감사 저장이 확인되지 않은 scope의 정리와 승격은 차단된다.

매 실행은 새 `qa-ymath-realuse-wn-*` 코드와 전용 생성 seal을 발급한다. 개발 호스트의 API
컨테이너 내부 접근 제한 파일에 복구 capability를 보관하고 관리자 출력에는 포함하지 않는다.
일회용 tenant의 처리가 끝나면 해당 R2 prefix와 정확한 AI job/result·Redis 키를 앞뒤로
조회하고 접근 토큰, 감사 활동, tenant와 계정 정리 결과를 확인한다. 새 작업을 덮어쓰거나
기존 FE QA seal을 채택하지 않는다. 성공 시 복구 파일을 지우고 건수·소유권 감사만 보존한다.

API 변경 요청의 응답이 끊기거나 정리가 완료되지 않으면 해당 단계에서 preprod 진입을 차단한다.
배포 담당자는 SSM 명령 ID와 같은 호스트·release를 유지하고 실행 중이던 요청과 작업의 종료를
확인한 뒤 같은 manifest·release·운영 DB 이름과 유효한 공유 lock 소유자로 스크립트의
`-CleanupOnly`를 실행한다. 성공 marker와 zero readback 이후에만 다음 교체를 진행한다.
이 단계에서 이전 인스턴스는 이미 종료되었다. 기존 검증 개발 기준으로 되돌릴 때는 배포
담당자가 교체 전 기록한 이전 API·AI·Tools·Messaging 네 digest, release ID, API와 worker
SSM 버전을 기존 `deploy-api-development.ps1`에 고정해 새 호스트를 정상 배포하고 유일
active·health·identity를 다시 확인한다. 정규 검사의 실패가 이 복구 배포를 자동 실행하지 않는다.

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
이 명령은 `SOLAPI_MOCK=true`, `MESSAGING_DRY_RUN_TRIGGERS` 빈 값인 상시 개발
런타임에서만 `clinic_cancelled`와 `clinic_reservation_created`의 승인된 공용 봉투 매핑을 확인하고, 일회용 QA
tenant에 tenant-owned content template과 활성 알림톡 config를 만든다. 학생+학부모
취소 접수 검증에는 합성 학생 연락처만 사용한다. 성공 판정은 취소 상태 저장과 두
durable outbox 생성, 전용 개발 SQS·mock worker 처리이며 실제 Solapi 호출은 0건이다.
마감 후 예약 신청은 학생·학부모·지정 교직원 3건, 뒤이은 승인은 학생·학부모 2건을
각 예약 발생 식별자로 구분해 개발 worker의 성공 로그와 `mock-` 제공자 ID로 확인한다.

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
- 일반 `VIDEO_BATCH_JOB_QUEUE/DEFINITION`은 빈 값이고 운영 Batch 제출이 불가능함

## 숙제 호환 영상 전용 development Batch

`run-homework-preview-development-canary.ps1` / `homework_preview_development.py`는
`params.yaml`의 `homeworkPreviewDevelopment`만 소유한다. 숙제 파생 영상 worker는
DB/R2만 사용하며 일반 강의 Video 파이프라인과 Redis는 활성화하지 않는다.

- 전용 Fargate CE/queue(최대 4 vCPU), ARM64 작업 2 vCPU/4GiB, 전용 job/execution role,
  14일 로그 그룹을 사용한다. 기존 development SG의 inbound 0개와 public subnet
  두 개의 IGW 경로를 검증한다. task에만 public IP를 부여하며 ingress를 추가하지 않는다.
  기존 자원의 소유권·설정이 다르면 자동 덮어쓰기 대신 실패한다.
  Batch readback이 로그 설정에 추가하는 빈 `secretOptions`는 미지정과 같은 값으로
  비교한다. 비어 있지 않은 로그/컨테이너 secret·mount·volume·ulimit와 이미지·역할·
  환경 버전·자원 설정 차이는 계속 drift로 거부한다.
- job role은 정확한 development workers parameter GetParameter만 허용하고 다른
  parameter 읽기·열거·history를 거부한다. execution role은 Video ECR pull과 전용 로그
  쓰기만 허용한다. API role에는 별도 소유 inline policy로 이 queue와 개발 definition
  prefix의 SubmitJob만 추가한다. 기존 parameter deny/runtime policy를 보존하고
  `Ensure-ApiDevelopmentIAM`도 추가 grant를 exact readback한다.
- OIDC의 development managed policy에 자원별 권한을 추가한다. main/ref, production
  environment trust와 production deploy inline policy는 유지한다. 적용 전 기존
  `converge-api-development-oidc.ps1`의 trust/readback 경로로 수렴한다.
- publisher의 `-EnableHomeworkPreview`는 workers env를 먼저 한 번 발행하고
  `academy-development-homework-video-<release SHA256 앞12자>-v<workers env version>:1`을
  API env에 고정한다. definition은 immutable Video digest와 versioned `BATCH_SSM_ENV`를
  사용한다. 같은 이름의 다른 revision/image/env는 거부한다. 최초 instance bootstrap은
  opt-in 없이 이전 검증 이미지로 접속 기반을 만든다.
- development workers parameter는 모든 설정값과 명시적인 격리용 빈 값을 보존한
  compact UTF-8 JSON으로 저장한다. 기존 base64(JSON)의 용량 증가 때문에 OMR
  opt-in을 포함한 값이 Advanced parameter 한도 8,192바이트를 넘었던 문제를 해결한다.
  production workers parameter와 production bootstrap의 base64 계약은 변경하지 않는다.
  개발 AI/Tools/Messaging 공통 bootstrap은 새 JSON과 기존 base64(JSON)를 읽고,
  잘못된 형식·UTF-8·환경/릴리스/DB/큐 경계·env 줄 삽입은 고정 오류로 거부한다.
  개발 숙제 Batch entrypoint도 기존의 JSON/base64 이중 reader를 사용한다.
- publisher는 첫 쓰기 전에 workers와 API의 UTF-8 바이트 수를 모두 검사한다.
  API의 homework definition에는 아직 발급되지 않은 workers version의 최대 Int64
  자릿수를 예약하며, 반환된 실제 version으로 바인딩한 값도 다시 검사한다.
  8,192바이트까지 허용하고 초과하면 `DEVELOPMENT_ENV_TOO_LARGE`와 종류·크기만
  보고한다. 게시 응답에 유효한 version이 없으면 `DEVELOPMENT_ENV_PUT_NO_VERSION`,
  해당 version의 전체 payload 또는 응답 version이 다르면
  `DEVELOPMENT_ENV_READBACK_MISMATCH`로 실패한다. 값·키·CLI 원문은 오류에 넣지 않는다.
  원격 게시/조회 실패 뒤 생성된 미사용 development version은 활성화 증거가 아니며,
  같은 후보로 정상 publisher를 재실행해 새 정확한 version 쌍을 검증해야 한다.
  `test_omr_scored_development_env.py`는 전체 publisher의 가짜 AWS 전송 경로,
  한글 포함 8,191/8,192/8,193바이트 경계, 새/기존 reader와 잘못된 입력을 검증한다.
- 공식 workflow와 수동 `deploy.ps1`은 후보 API 활성화 전에 codec canary를 요구한다.
  현재 active development API container의 instance role로 고정 명령을 SubmitJob한다.
  제품과 같은 SubmitJob-only 권한으로 검사하며 추가 TagResource 권한을 요구하는 작업
  태그는 보내지 않는다. 전용 queue/definition, 고유 jobName과 반환된 job ID로 소유권을
  확인하고 인프라 자원의 기존 소유 태그는 유지한다.
  IAM 정책 readback과 지역별 권한 적용 사이의 전파 지연은 명시적 AccessDenied 응답에만
  최대 45초 동안 5초 간격으로 재시도한다. SDK 자동 재시도는 끄고, timeout·5xx 등
  접수 여부가 불확실한 응답은 재제출하지 않는다. 영구 거부는 계속 release를 차단한다.
  Batch에서 개발 DB/역할, 운영 DB CONNECT 거부, 운영 worker parameter·R2 거부,
  합성 HEVC R2 왕복, H.264 변환과 실제 RGB 프레임 디코딩을 검증한다. DB 거부는 기존
  개발/preprod gate와 같은 `has_database_privilege(current_user, 대상 DB, 'CONNECT')`
  결과가 정확히 false인지 확인한다. 초기 연결 FATAL의 빈 SQLSTATE를 접근 허용으로
  오판하지 않으며, 역할·DB 불일치, 조회 실패와 불명확한 결과는 거부한다. R2는 기존
  개발 gate와 같은 403/404/AccessDenied/NoSuchBucket만 거부 증거로 인정한다.
  실패 로그에는 고정 allowlist의 검사 코드 또는 `unexpected_error`와 예외 유형만
  남기며 연결정보·SDK/DB 오류 원문은 출력하지 않는다. tenant/user 행은
  만들지 않고 `qa-homework-preview/<정확한 Batch UUID>/`의 두 객체만 쓴다.
- 성공·실패 모두 terminal job 확인 뒤 같은 두 key만 SSM으로 삭제하고 prefix 잔여 0을
  재조회한다. 제출 응답 유실은 고유 jobName으로 재조회하며 중복 제출하지 않는다.
  timeout은 정확한 job을 terminate하고 terminal 확인 전에는 객체를 지우지 않는다.
  상태 불명/cleanup 실패는 release를 차단하며 evidence의 job ID로 복구한다.

Plan은 identity/image/network만 조회한다. Apply는 각 mutation마다 기존 shared deployment
lock의 owner/TTL을 확인하므로 소유 release window에서만 실행한다. nonsecret 결과는
CI artifact `homework-preview-development-canary`에 보존한다. success, 실제 frame,
R2 cleanup0 없이는 후보 API 활성화와 preprod로 진행하지 않는다. 기존 XLSX/PPTX/R2,
preprod 종료, migration/rolling/health, same-artifact frontend QA 게이트는 유지한다.
앱의 제출→교직원 재생/검수→reload는 별도 disposable tenant의 실제 API journey에서
확인한다. 일반 Video/Redis canary의 상태는 아래와 같다.

## Video Batch 개발 canary 준비 계약 [PROPOSED]

일반 강의 Video 파이프라인의 development Batch 자원이 준비될 때 추가할 canary는 production 이름을
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

The synthetic Tools smoke also invokes the public-resource subprocess entrypoint on generated HWPX and XLSX documents in the exact candidate image, retaining its credential-free environment, network denial and resource limits. It requires all expected Korean glyphs (ignoring extraction whitespace) and preserved document pages before development promotion. Synthetic spreadsheet columns are sized in the source so the layout-preserving renderer does not clip the test label; all files live in a temporary directory and create no tenant rows.

## 초기 계정 등록 개발 후보 검증

고정 SSM `academy-frontend-development-qa`의 `AccountProbe` action은 기존
개발 env/DB/IAM resource-denial·release/digest·생성 ownership capability 확인 뒤만
`probe-account-registration-development.py`를 로드한다. 정확한 생성 tenant ID와
양의 bounded `AccountStudentId`, `AccountProbeMode=verify|snapshot|compare`,
`AccountProbeKind=fixed|phone_last4|random`만 받으며 임의 URL·명령·비밀번호 인자는 없다.
일반 Inspect/Setup/Cleanup은 계정 인자를 기본값으로만 허용한다. 제품 인증 API를
추가하지 않으며 runtime 문서 수렴·SSM 실행은 후보 release gate 이후 release owner가 한다.
tenant 조회의 `SELECT FOR UPDATE`와 생성 소유권 확인은 짧은 `transaction.atomic()`
안에서 수행한다. probe의 실제 HTTP 인증 전에 이 트랜잭션을 끝내 tenant 행 잠금을
해제하며, 인증 요청이 자신의 외래키 쓰기에 필요한 잠금을 기다리게 하지 않는다.

대상은 해당 disposable tenant의 `qa-account-registration-{tenant code SHA256 앞12자}-`
이름 prefix 학생과 연결된 동일 tenant 학생/학부모 User·활성 역할 membership이다.
현재 안내 암호문을 메모리에서 복호화하고 setter 없는 raw/hash 대조를 거친 뒤
proxy/redirect를 거부하는 `127.0.0.1:8000` token·core/me·student/me 실제 HTTP 인증을 한다.
학부모 student/me에는 exact 학생 ID를 X-Student-Id로 보내고 두 역할의 응답 ID가
그 학생과 일치해야 한다. 다른 자녀로의 fallback은 실패한다. 랜덤은 숫자 6자리, 전화 방식은 각 User 자신의 전화 뒤4자리도 확인한다.
기존 비밀번호를 알 수 없으면 `credential-unrecoverable`로 실패한다. pending 임시암호를
소비하거나 초기화/재안내 resolver로 값을 새로 만들지 않는다.

snapshot은 정확한 학부모 hash의 keyed commitment를 QA 전용 OpsAuditLog에만 저장한다.
compare는 동일 scope/학부모와 해시 보존을 대조하고 성공 시 관찰 행을 삭제한다.
원문·암호문·password hash·commitment·전화·token은 stdout/SSM/로컬 응답에 포함하지 않는다.
출력은 닫힌 schema의 boolean·count·scope SHA256·고정 mode/kind뿐이다.
인증의 last_login/token/audit 관찰 쓰기는 허용하지만 대상 Student/Parent/User의
나머지 persisted field는 인증 전후 동일해야 한다. 전체 개발 cleanup은 기존 provenance와
resource 경계를 유지하고 자기 scope의 snapshot 잔여도 지운 뒤 인증 관찰/사용자/tenant 0을
확인한다. foreign snapshot은 삭제하지 않고 cleanup을 실패시킨다.

프론트엔드 `student-parent-registration-realuse.spec.ts` 7건은 실제 저장·reload와
직접 입력/전화 방식 두 역할 UI 로그인, 랜덤 내부 실제 인증, 기존 Parent 보존,
번호 없는 학생 오류 복구, 신청 선택 유지·승인을 검증한다. 랜덤 내부 인증은
브라우저에 랜덤 원문을 직접 입력한 증거와 구분한다. probe 실패/없음/schema 불일치와
skip은 release 실패다. 기존 운영 read-only 조건과 실발송 제한은 변경하지 않는다.
기존 공식 CI entry인 `python -B -m unittest scripts.v1.test_frontend_development_qa -v`가
고정 SSM 계약과 별도 probe 안전성 테스트를 함께 실행한다. probe 단독 확인은
`python -B -m unittest scripts.v1.test_probe_account_registration_development -v`를 사용한다.
PostgreSQL 회귀 검사는 `apps/core/tests/test_development_account_probe.py`의
`TransactionTestCase`가 실제 고정 action의 잠금 조회와 인증 호출 전 트랜잭션 종료를
검증한다. SQLite 및 암묵적 트랜잭션을 사용하는 일반 `TestCase` 통과로 대체하지 않는다.
