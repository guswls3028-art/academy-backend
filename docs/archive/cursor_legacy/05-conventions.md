# Conventions (Cursor 작업 시 규칙)

## 1. 문서와 코드

- **코드가 진실**: 문서와 코드가 다르면 **코드를 따르고**, 문서를 코드에 맞게 수정한다.
- **추측 금지**: 동작·필드·경로는 반드시 실제 코드/설정에서 확인한 뒤 문서에 쓴다.
- **docs_cursor**: Cursor가 작업할 때 이 폴더 문서만으로도 필요한 정보를 얻을 수 있게 유지한다.

## 2. Core 봉인 (apps/core)

- **CORE_SEAL.md** 가 헌법. 다음은 봉인 위반으로 간주:
  - tenant resolve fallback 추가
  - Program write-on-read
  - TenantDomain primary 다중 허용
  - host 외 식별자 기반 멀티테넌트
  - core에 과금/요금제/워커 로직 추가
- 허용 확장: TenantDomain 운영 필드, Program.feature_flags/ui_config, TenantMembership role, core **외부** 도메인에서의 정책.

## 3. API · 권한

- View 권한은 `permissions.py` 클래스만 사용. View 내부에서 role로 분기하지 않는다.
- 프론트 권한 SSOT: `/api/v1/core/me/` 의 `tenantRole`. 프론트에서 role 추론 금지.

## 4. 테넌트

- Tenant 결정은 **Host 기반만**. Header/Query/Cookie/Env fallback 금지.
- 새 도메인 추가 시: TenantDomain 등록 + ALLOWED_HOSTS, CORS, CSRF 반영.

## 5. 문서 수정 시

- README, CORE_SEAL, docs_cursor, docs/SSOT_* 등 수정 시 **실제 파일/코드 경로·내용**과 일치시킨다.
- 존재하지 않는 문서 참조 제거하거나, 해당 문서를 만든 뒤 링크한다.

## 6. SSOT 날짜 폴더 (SSOT_0218 이후)

- **폴더**: `docs/SSOT_MMDD/` (MMDD = 해당일 4자리). 그 안에 **cursor_only/** (AI 전용), **admin97/** (사람용) 반드시 둠.
- **cursor_only**: AI가 읽기 좋은 형태만. 자연어 불필요. 사용자 미확인.
- **admin97**: 사람이 보는 문서만. 가이드·체크리스트·배포 안내 등은 모두 admin97에만 작성.
- 상세: `.cursor/rules/ssot-folder-structure.mdc`
