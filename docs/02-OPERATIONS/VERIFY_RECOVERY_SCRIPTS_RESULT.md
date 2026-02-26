# 복구 스크립트 검증 결과

**검증 일시**: 2026-02-22  
**방식**: 실제 실행 + 키 검사 로직 테스트 (가짜 SSM)

---

## 1. verify_ssm_api_env.ps1

| 항목 | 결과 |
|------|------|
| 실행 | 실행됨. SSM get 시 이 환경에서 AWS 자격증명 무효 → **exit 1** 정상 처리. |
| 실패 시 메시지 | "Run: .\scripts\upload_env_to_ssm.ps1 (with full .env...)" 출력 확인. |
| 키 검사 로직 | 가짜 SSM 문자열로 테스트: 필수 키 전부 있으면 "OK all required keys present", DB_NAME/DB_USER/R2_ENDPOINT 누락 시 "Missing: DB_NAME, DB_USER, R2_ENDPOINT" 정확히 도출. |

**결론**: SSM get 실패·키 누락 시 exit 1 및 안내 메시지 정상. 필수 키 전부 있을 때만 exit 0.

---

## 2. deploy_api_on_server.sh

| 항목 | 결과 |
|------|------|
| 문법 | bash -n 불가(이 환경에 bash 없음). 코드 열람 기준 문법 문제 없음. |
| REQUIRED_KEYS | **수정함**: 기존 `DB_HOST R2_ACCESS_KEY R2_SECRET_KEY R2_ENDPOINT REDIS_HOST` → **DB_NAME, DB_USER 추가**. DB_NAME/DB_USER 없이 배포되면 Django DATABASES에 null 남아 500 발생하므로 가드에 포함. |

**결론**: 배포 전에 DB_HOST·DB_NAME·DB_USER까지 검사하도록 맞춤.

---

## 3. verify_api_after_deploy.sh

| 항목 | 결과 |
|------|------|
| 문법 | bash -n 불가(이 환경에 bash 없음). 코드 열람 기준 문법 문제 없음. |
| 로직 | `docker exec academy-api python -c "..."` → DATABASES JSON에서 `"HOST": null`, `"NAME": null` grep 시 exit 1. curl로 backlog-count 호출, 200 아니면 exit 1. ENV_FILE에서 LAMBDA_INTERNAL_API_KEY 추출 후 헤더로 전달. |

**결론**: EC2에서 deploy 직후 실행 시, HOST/NAME null 또는 backlog-count 비 200이면 실패하도록 설계됨.

---

## 4. 이 환경 제약

- **AWS**: `UnrecognizedClientException`으로 SSM get 불가. 로컬에서 유효한 자격증명으로 `.\scripts\verify_ssm_api_env.ps1` 실행해야 SSM 실제 내용 검증 가능.
- **EC2/도커**: SSH·docker 미실행. `deploy_api_on_server.sh`, `verify_api_after_deploy.sh`는 EC2에서 직접 실행해야 함.

---

## 5. 요약

| 스크립트 | 검증 내용 | 결과 |
|----------|-----------|------|
| verify_ssm_api_env.ps1 | SSM 실패 시 exit 1, 키 누락 시 missing 목록, 전부 있으면 exit 0 | 통과 |
| deploy_api_on_server.sh | REQUIRED_KEYS에 DB_NAME, DB_USER 추가 | 수정 반영 |
| verify_api_after_deploy.sh | HOST/NAME null 및 backlog-count 200 검사 로직 | 코드 기준 정상 |

**실제 SSM 값 검증**은 로컬에서 AWS 설정 후 `.\scripts\verify_ssm_api_env.ps1` 한 번 돌리면 됨.  
**배포·재현성 검증**은 EC2에서 `deploy_api_on_server.sh` → `verify_api_after_deploy.sh` 순서로 실행하면 됨.
