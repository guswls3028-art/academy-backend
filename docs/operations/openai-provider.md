# OpenAI 공급자·모델 운영 계약

2026-11-05 Gemini 생성형 AI 호출을 OpenAI로 대체한다. 사용자 제공 키는
로그·Git·채팅에 남기지 않고 기존 환경 저장소를 통해서만 주입한다.

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
대상은 /academy/api/env(JSON), /academy/workers/env(base64 JSON)의 OPENAI_API_KEY와
위 모델 설정이다. 구 버전과의 공존 기간에는 Gemini 키와 기존 adapter 선택 이름을
보존한다. 새 코드는 gemini_flash/gemini_flash_lite 설정을 OpenAI rollout alias로만
인식하며 Google endpoint를 호출하지 않는다. 새 adapter 값은 openai다.

키·설정 변경은 fresh clean main, release lock, version readback과 기존 release
절차 아래 실행한다. 먼저 사용자가 승인한 같은 키를 개발 전용 SecureString
/academy/providers/development/openai-api-key에 보관하고, 기존 CI 역할
academy-gha-ecr-build에는 DevelopmentOpenAiKeyRead SID로 해당 단일 경로의
ssm:GetParameter만 허용한다. 운영 key/env를 먼저 바꾸지 않는다. IAM 정합화는
기존 소유 helper와 공유 잠금·fresh source 절차를 따르며 현재 implicitDeny를
우회하지 않는다. publisher는 이 정확한 개발 source를 읽은 뒤에만 환경을 발행한다.
동일 immutable 후보의 격리개발 실사용(합성/승인 fixture)·preprod를 통과한 후
운영 전환 단계에서 API/worker의 OPENAI_API_KEY를 반영한다. 개발 worker만 승인된 OpenAI 키를
재사용하며 API와 preprod는 외부 AI 키 제거를 유지한다. 개발 수동 API 정밀분석은
키 없는 경계에서 명시적 실패를 검증하고 정상 모델 호출은 개발 worker에서 검증한다.
기존 DB/R2/SQS 격리, QA tenant/user cleanup zero, AI DLQ HOLD는 변경하지 않는다.

새 API/AI/Tools revision·일반 사용 성공·reload/후속 역할·오류 복구가 확인된 뒤
운영 adapter 설정을 openai로 정리하고 Gemini 키를 환경에서 제거한다. 기존 키의
제공자 계정 자체를 폐기하지 않는다. 롤백은 이전 immutable 이미지와 이전 환경
버전을 함께 복원하며 새로운 결과/수동 수정/기존 벡터를 삭제하지 않는다.

검증: VLM 좌표/grounding/게이트, 공급자 request·거절·불완전 출력·quota 재시도,
교사 문체·전사·리뷰 테스트와 candidate-env 격리 계약, exact 후보 CI 및
[개발환경 계약](persistent-development-runtime.md)의 실사용 증거가 필요하다.

2026-11-05 공식 Standard short-context 요금(USD/백만 토큰)은 Luna 입력0.10/출력0.50,
Terra 입력1.00/출력5.00, Sol 입력2.00/출력10.00이다. 30분 cache write는 각각
0.125/1.25/2.50이며 모델·처리 tier·긴 context에 따라 달라진다.
[요금 출처](https://developers.openai.com/api/docs/pricing). 합성 2문항 후보 어댑터
검증은 Terra 입력1955(그중 cache write1952), 출력137 토큰, 5.24초였다. 이는
공급자/좌표 계약의 독립 검증이며 실제 교재 품질이나 전체 실사용 검증의 대체가 아니다.
