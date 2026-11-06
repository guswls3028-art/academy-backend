# OpenAI 공급자·모델 운영 계약

2026-11-05 Gemini 생성형 AI 호출을 OpenAI로 대체한다. 사용자 제공 키는
로그·Git·채팅에 남기지 않고 기존 환경 저장소를 통해서만 주입한다.
새 이미지의 중앙 `resolve_openai_api_key`는 `ACADEMY_OPENAI_API_KEY`,
`OPENAI_API_KEY`, `EMBEDDING_OPENAI_API_KEY` 순으로 읽는다. AIConfig의 기존
필드명은 호환을 유지하며 VLM·Matchup 활성화·Excel도 같은 resolver를 사용한다.
운영 `1b609`는 새 이름을 읽지 않는다. 전환 시 기존 두 legacy 키를 덮어쓰지
않으므로 새 키가 구 이미지의 Bedrock/Gemini 경로를 OpenAI로 바꾸지 않는다.

| 용도 | 기본 모델 | 설정 |
|---|---|---|
| 페이지 역할·문항 crop 분류 | gpt-6-luna | MATCHUP_VLM_TEXT_MODEL, MATCHUP_HYBRID_VLM_MODEL |
| 페이지 문항 위치·사진 전사 | gpt-6.1-terra | MATCHUP_VLM_VISION_MODEL, PROBLEM_TRANSCRIPTION_MODEL |
| 문제 생성·풀이·검수 보고서 | gpt-6.1-sol | PROBLEM_GEN_MODEL, PROBLEM_STUDIO_EXPLANATION_MODEL |
| Excel 열 의미 분류 | gpt-6-luna | EXCEL_SCHEMA_INFER_MODEL |
| 검색 임베딩 | 기존 local / text-embedding-3-small 유지 | EMBEDDING_BACKEND, EMBEDDING_OPENAI_MODEL |

모델 계층은 [OpenAI 모델 안내](https://developers.openai.com/api/docs/models),
매개변수는 [이전 안내](https://developers.openai.com/api/docs/guides/latest-model#migration-quickstart)를
기준으로 한다. 제공된 키로 각 선택 모델의 합성 텍스트/이미지 응답을 확인했다.
기본 추론은 low, 풀이·생성은 medium이며 이미지 OCR grounding은 medium이다.
이 모델들에는 temperature를 보내지 않는다. 기존 명시적 구모델 override는 지원한다.

## 권한·출력·비용

기존 tenant/staff 접근권한, PII 마스킹, 수동 수정과 교사 승인 데이터는 유지한다.
생성·분석 결과는 검수 제안이며 canonical 정답/학생 데이터에 자동 반영하지 않는다.
이미지 좌표는 0..1000 yxyx 계약과 원본 픽셀 변환·공유 지문 검증을 유지한다.
OpenAI 요청은 store=false. 완료/거절/잘린 응답을 검사한 뒤에만 결과를 사용한다.
오류에 provider 응답 본문이나 원문·키를 넣지 않는다. VLM 자동 처리 실패는 기존
문항을 보존하고 debug 실패를 남기며, 수동 정밀분석 API는 빈 성공 대신 재시도 가능한
502를 반환한다. 해당 API는 실제 tenant_id를 quota 경계까지 전달한다.

기존 문서 50회/tenant 일일 500회와 hybrid 200회 제한을 유지한다. VLM의 429/5xx
단발 재시도도 호출 예산을 소비한다. text는 출력 2048, crop 분류는 1024,
vision은 MATCHUP_VLM_MAX_OUTPUT_TOKENS(기본8192)로 제한한다. 고정 달러 금액을
보장하는 제한은 아니며 모델/토큰별 사용량을 배포 검증에서 기록한다.
전체 페이지를 무조건 AI로 보내지 않고 기존 low-confidence/tenant 게이트를 유지한다.

임베딩 알고리즘·차원·기존 벡터는 변경하지 않는다. 운영의 기존 EMBEDDING_BACKEND는
미설정이므로 기본값을 local로 명시한다. 새 범용 OpenAI 키가 local 모델 오류 때
1536차원 OpenAI fallback을 활성화해 기존 벡터와 섞이지 않도록 한다. 명시적인
openai/auto override는 유지한다. 운영 local 모델 로드는 배포 readback으로 확인한다.
키가 없는 환경의 기존 Bedrock fallback은 롤백 호환용으로 유지한다. 키가 있는
OpenAI 호출 실패를 다른 유료 공급자로 조용히 재시도하지 않는다.

## 전환·복구

배포 총괄이 현재 실행을 종료/공식 unlock한 뒤 exact 후보를 인수한다.
대상은 /academy/api/env(JSON), /academy/workers/env(base64 JSON)의
ACADEMY_OPENAI_API_KEY와 EMBEDDING_BACKEND=local 두 값뿐이다.
legacy OpenAI/embedding 키와 명시적 모델 override는 변경하거나 전환 키로 사용하지
않는다. 구 버전과의 공존 기간에는 Gemini 키와 기존 adapter 선택 이름을
보존한다. 새 코드는 gemini_flash/gemini_flash_lite 설정을 OpenAI rollout alias로만
인식하며 Google endpoint를 호출하지 않는다. 새 adapter 값은 openai다.

키·설정 변경은 fresh clean main, release lock, version readback과 기존 release
절차 아래 실행한다. 먼저 사용자가 승인한 같은 키를 개발 전용 SecureString
/academy/providers/development/openai-api-key에 보관하고, 기존 CI 역할
academy-gha-ecr-build의 별도 `academy-gha-openai-runtime` 관리 정책에
DevelopmentOpenAiKeyRead SID로 해당 단일 경로의 ssm:GetParameter를 허용한다.
추가 쓰기는 운영 workers env의 PutParameter 하나다(API Get/Put, workers Get은
기존 정책에 있다). Tier/KMS 보존에는 지역으로 제한한 DescribeParameters가
필요하다. 이 API의 IAM Resource는 `*`지만 호출의 Name 필터는 정확한 두 대상만
사용하며 값 조회/쓰기를 확장하지 않는다. 인라인 정책 한도 때문에 이 세 권한을
독립 관리 정책으로 정합화하고 전체 내용·정확한 세 attached 정책을 readback한다.
개발 정책 자체의 권한/운영 자원 경계는 확대하지 않는다. 운영 key/env를 먼저 바꾸지 않는다. IAM 정합화는
기존 소유 helper와 공유 잠금·fresh source 절차를 따르며 현재 implicitDeny를
우회하지 않는다. publisher는 이 정확한 개발 source를 읽은 뒤에만 환경을 발행한다.
동일 immutable 후보의 격리개발 실사용(합성/승인 fixture)·preprod를 통과한 후
운영 전환 단계에서 API/worker의 ACADEMY_OPENAI_API_KEY와 EMBEDDING_BACKEND=local을
각 환경 payload에 함께 반영한다. 구·신 이미지 공존 중에는 명시적 모델 override를
추가하지 않고 각 이미지의 호환 기본값을 사용한다. 적용 직전의 SSM 버전과
현재 successful manifest를 다시 읽어 롤백 기준으로 보존하며 과거 manifest로
최신 배포 기록을 덮어쓰지 않는다. 개발 worker만 승인된 OpenAI 키를
새 이름으로 재사용하며 legacy 두 키는 제거한다. 개발 API와 preprod는 새 이름까지
포함해 외부 AI 키 제거를 유지한다. 개발 수동 API 정밀분석은
키 없는 경계에서 명시적 실패를 검증하고 정상 모델 호출은 개발 worker에서 검증한다.
기존 DB/R2/SQS 격리, QA tenant/user cleanup zero, AI DLQ HOLD는 변경하지 않는다.

정상 workflow의 두 번째 `production` 승인이 있는 `run-migrations`에서만
`publish_openai_production_env.py`를 동기 실행한다. 동일 후보 development/preprod
성공 → freshness/manifest 검사 → cutover 계획 저장 → 새 키 게시·양쪽 readback →
기존 account open/drain·migration·rolling 순서다. 게시 단계는 개발에서 검증한
정확한 worker parameter version과 release ID를 받고 그 새 키가 승인된 개발
source와 일치하는지 메모리 안에서 검사한다. 별도 수동 게시와 CI를 경합시키지 않는다.
설정 게시 자체는 두 번의 순차 쓰기다. 기존 프로세스 환경을 바꾸지 않고, 새로
뜨는 구 이미지도 새 키를 인식하지 않는다. local 모델 준비와 affected-flow 성공은
별도 실제 이미지 검증이 필요하다.

첫 SSM 쓰기 전에 같은 배포 잠금의 별도 `runtimeEnvTransition`에 원본/개발/source
버전·후보·쓰기 의도만 저장하고 TTL을 고정한다. 키/원본 payload는 저장하지 않고
`accountWindow`를 빌리지 않는다. 반환 버전·현재 최신 값·Tier/KMS를 모두 확인하기
전에는 migration을 시작하지 않는다. 명시적 거절이나 확인된 이번 쓰기만 원래 raw
값으로 새 버전을 만들어 보상한다. 응답 유실 후 최신 값이 아직 원본인 것은
미적용 증거가 아니므로 재시도/보상/자동 unlock하지 않는다. drift·소유권 상실·복구
실패·runner 취소에도 pin은 남고 `deployment_lock.py release/acquire/renew`가 이를
보존한다. 워크플로의 최종 cleanup이 실행되어도 이 pin을 무시할 수 없다.

복구는 배포 총괄이 소유한다. 수동 작업의 fresh clean main/manifest·공유 잠금
절차를 먼저 적용하고 같은 잠금의 원래 owner/source/release/developmentVersion으로
publisher의 `--recover`를 사용한다(CI 환경변수를 위조하지 않는다). 원본 SSM
version과 당시 개발/source version을 읽어 이번 intent의 쓰기임을 확인한 경우만
복구하고 양쪽 원본의 readback 후 pin을 제거한다. 아직 결과가 불명확하거나 제3자
변경이 있으면 쓰지 않고 중단한다. 원본 version이 유실됐거나 lock owner가 달라진
경우에도 강제 해제하지 않는다. 성공 게시 후 이후 rolling 실패는 기존 same-run
이미지 보상과 이 환경 복구의 정확한 상태를 함께 판단하며 blind restore를 하지 않는다.

개발 publisher와 `core/sync_env.ps1`의 SecureString 쓰기는 기존 Python/boto3를
사용하는 `ssm_secure_put.py`로 UTF-8 stdin만 전달한다. 값은 자식 프로세스의
명령 인자·환경변수·임시 평문 파일·출력에 넣지 않는다. 응답은 version/tier만,
실패는 고정 오류코드만 전달한다. 정상 개발 배포 job은 기존 constraints의
boto3/botocore pin을 설치하며 수동 실행도 해당 SDK가 있는 Python을 사용한다.
SDK 부재는 쓰기 전에 실패한다. 불명확한 쓰기를 helper가 재시도하지 않는다.
stdin 전송·프로세스 종료·stdout/stderr 수집은 하나의 제한시간을 공유한다.
자식이 입력을 읽지 않거나 후손이 출력 pipe를 계속 열고 있어도 고정 timeout
오류로 종료하며, 정리 과정에서 무제한 종료 대기를 추가하지 않는다.
두 parameter는 순차 쓰기이며 기존 부분 실패·보상·exact readback 절차를 유지한다.
`candidate_prepare_sources.py`의 프로젝트/회전 메타데이터 검증은 별도 미래
candidate 경로의 계약이며 현재 정상 6이미지 release의 선행조건이 아니다.

AWS SDK가 필요한 실행 host는 `verify-api-development`, `run-migrations` job과 수동
`deploy.ps1`/`initialize-api-development.ps1` host다. 수동 host는 실행 전에
동일 PATH의 `python -c "import boto3, botocore"`가 성공하는 기존 환경을 사용한다.
`core/sync_env.ps1`의 운영·보상·preprod candidate 쓰기는 수동 `deploy.ps1`
경로다. 독립 `verify-api-preprod` job은 별도 `publish-api-preprod-env.ps1`의
기존 CLI publisher를 사용하며 이번 helper를 호출하지 않는다. canary runner가
`sync_env.ps1`을 로드하는 것만으로 해당 쓰기 함수가 실행되는 것은 아니다.
제품 OpenAI SDK는 AI에 있던 동일 `openai` requirement를 API/Tools에도 선언한다.
기존 constraints와 의존성 버전 계약을 재사용하고 버전 업그레이드/별도 lock을 만들지
않는다. 테스트의 mocked SDK 통과는 이미지 설치 증거가 아니다. exact image의 SDK
import 및 기존 승인된 synthetic provider smoke는 격리개발의 release 검증에 속한다.

새 API/AI/Tools revision·일반 사용 성공·reload/후속 역할·오류 복구가 확인된 뒤
운영 adapter 설정을 openai로 정리하고 Gemini 키를 환경에서 제거한다. 기존 키의
제공자 계정 자체를 폐기하지 않는다. 실패한 실행은 그 실행의 검증된 pin-state 보상과
환경 복구 절차를 사용한다. 완료된 stateful API 배포를 임의 과거 이미지로 돌리지 않고
공식 immutable roll-forward 절차를 따른다. 새 결과/수동 수정/기존 벡터는 삭제하지 않는다.

검증: VLM 좌표/grounding/게이트, 공급자 request·거절·불완전 출력·quota 재시도,
교사 문체·전사·리뷰 테스트와 candidate-env 격리 계약, exact 후보 CI 및
[개발환경 계약](persistent-development-runtime.md)의 실사용 증거가 필요하다.

2026-11-05 공식 Standard short-context 요금(USD/백만 토큰)은 Luna 입력0.10/출력0.50,
Terra 입력1.00/출력5.00, Sol 입력2.00/출력10.00이다. 30분 cache write는 각각
0.125/1.25/2.50이며 모델·처리 tier·긴 context에 따라 달라진다.
[요금 출처](https://developers.openai.com/api/docs/pricing). 합성 2문항 후보 어댑터
검증은 Terra 입력1955(그중 cache write1952), 출력137 토큰, 5.24초였다. 이는
공급자/좌표 계약의 독립 검증이며 실제 교재 품질이나 전체 실사용 검증의 대체가 아니다.

마이그레이션 `0026_neutral_vlm_provider_label`은 `SeparateDatabaseAndState`의
빈 `database_operations`로 VLM 선택지 표시명만 갱신한다. DB 컬럼·인덱스·
저장된 `engine=vlm` 값은 유지되며 구·신 이미지가 같은 데이터를 사용할 수 있다.
