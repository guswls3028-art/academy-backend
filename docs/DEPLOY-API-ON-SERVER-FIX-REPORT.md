# 원격 배포 스크립트 실패 수정 보고서

## 1) 현재 실패 원인

- **실패 위치**: `scripts/deploy_api_on_server.sh` 1단계(env 생성) 및 2단계(Guard).
- **직접 원인**: SSM `/academy/api/env` 값이 **JSON**(또는 base64 인코딩된 JSON)으로 저장되어 있는데, 스크립트는 값을 **plain text**(탭/줄바꿈으로 구분된 `KEY=value` 줄)로 가정하고 `sed 's/\t/\n/g' | grep -v '^$'`만 적용해 `.env`에 썼다. JSON 한 줄이 그대로 들어가거나 형식이 깨져, `grep -E "^DB_HOST="` 등이 매칭되지 않아 Guard에서 "Required env missing"으로 중단됨.
- **누락 env가 생긴 이유**: 키가 SSM에 없어서가 아니라, **SSM 값 형식(JSON)과 스크립트 기대 형식(KEY=value 줄) 불일치**로 `.env` 내용이 Guard가 기대하는 형태로 생성되지 않았음.

## 2) 사실 기반 구조 정리

- **DB/R2/REDIS env가 오는 곳**:
  - **API Launch Template userdata** (`scripts/v1/resources/api.ps1`): SSM `/academy/api/env`를 `--output text`로 가져온 뒤 **JSON 파싱**하여 `KEY=value` 줄로 변환해 `/opt/api.env`에 쓰고, `docker run --env-file /opt/api.env` 사용.
  - **refresh-api-env.sh** (`scripts/v1/inline/refresh-api-env.sh`): 동일하게 SSM 값을 **JSON**으로 파싱해 `/opt/api.env`에 기록.
  - **update-api-env-sqs.ps1**: SSM `/academy/api/env`를 읽을 때 **JSON 또는 base64(JSON)** 로 가정하고, SQS 키 추가 후 같은 형식(JSON/base64)으로 put.
- **결론**: 설계상 `/academy/api/env` **한 곳**에 DB_HOST, DB_NAME, DB_USER, R2_*, REDIS_HOST 등이 들어 있어야 하고, 저장 형식은 **JSON**(또는 base64 JSON). `/academy/api/env`만으로 충분한 구조이며, 다른 SSM/기존 .env 병합은 불필요함.

## 3) 수정 설계

- **최소 수정**: `deploy_api_on_server.sh`만 변경. SSM 읽기 후 **JSON(및 base64 JSON) 파싱**으로 `.env`를 생성하도록 해서, refresh-api-env.sh / api.ps1 userdata와 동일한 방식으로 맞춤. Parameter Store 구조나 Guard 필수 키 목록은 그대로 둠.
- **반복 배포 안전성**: 매 배포마다 SSM에서만 가져와 `.env`를 **덮어쓰기**. 단일 소스(SSM)로 동일 스크립트를 여러 번 실행해도 동일 결과. 기존 .env merge는 하지 않음(설계상 SSM 단일 소스).

## 4) 실제 수정 파일 목록

| 파일 | 변경 목적 | 핵심 변경점 |
|------|-----------|-------------|
| `scripts/deploy_api_on_server.sh` | env 생성 형식 정합성 및 Guard 메시지 명확화 | ① SSM 값을 변수로 받은 뒤 JSON 파싱(또는 base64 디코드 후 JSON 파싱)으로 `KEY=value` 줄 생성 후 `.env` 덮어쓰기. ② JSON/base64 실패 시 기존처럼 plain 줄 형태로 fallback. ③ Guard 실패 시 SSM 키 보강 안내 문구 추가. |

## 5) 배포 스크립트 변경 핵심

- **.env 생성 방식**
  - **Before**: `aws ssm get-parameter ... --output text` 파이프 → `sed 's/\t/\n/g' | grep -v '^$'` → `.env` 덮어쓰기. (JSON이면 한 줄 또는 비정형으로 들어가 Guard 실패.)
  - **After**: SSM 값을 변수에 저장 → (선택) base64 디코드 → **JSON 파싱 후 `KEY=value` 줄로 변환**해 `.env` 덮어쓰기. 실패 시 plain 줄 fallback.
- **Guard 기준**
  - **Before/After 동일**: `DB_HOST`, `DB_NAME`, `DB_USER`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT`, `REDIS_HOST` 필수. 하나라도 없으면 exit 1. 변경점은 **에러 메시지**에 SSM 파라미터 및 문서 참조 안내 추가.

## 6) 실행 절차

- **원격 서버에서 배포**: API 서버(ec2-user)에 SSH 접속 후  
  `cd /home/ec2-user/academy && bash scripts/deploy_api_on_server.sh`  
  한 번 실행으로 env 생성 → migration → API 재시작까지 진행.
- **추가 SSM 수정**: 스크립트는 수정만으로 동작. 다만 SSM `/academy/api/env`에 위 필수 키들이 **JSON 객체**로 들어 있어야 Guard를 통과함. 이미 JSON으로 넣어 둔 상태라면 추가 작업 없음. 값이 아예 비어 있거나 키가 없다면, 로컬 `.env` 등에서 해당 키를 채운 뒤 JSON으로 put(또는 기존에 쓰던 bootstrap/업로드 절차) 필요.

## 7) 남은 TODO

- **필수**: 원격 서버에서 `bash scripts/deploy_api_on_server.sh` 1회 실행해 env 생성 → migration → API 기동이 끝까지 성공하는지 확인.
- **선택**: SSM `/academy/api/env` 최초 생성/갱신 절차를 문서(예: V1-OPERATIONS-GUIDE.md)에 명시해, 신규 환경이나 키 추가 시 참고할 수 있게 하기.
