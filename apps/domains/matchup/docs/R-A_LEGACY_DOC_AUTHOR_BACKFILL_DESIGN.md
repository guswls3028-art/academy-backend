# R-A 설계 문서 — Legacy 매치업 doc author CSV 매핑 백필

**상태:** 백로그 (2026-05-03 사용자 정책 결정)
**정책:** A-1 + A-3 (NULL 유지 default + 향후 CSV 매핑 옵션). A-4 자동 추론 **금지**.

---

## 배경

매치업 강사 1인 정체성 도입(2026-05-03)으로 `MatchupDocument.author` FK 추가. 신규 업로드는 `request.user`를 author로 자동 등록. 기존 운영 doc은 `author=NULL` (공용 풀 보호).

학원이 운영 정책에 따라 legacy doc을 강사별로 귀속하고 싶을 때 사용할 도구.

## 정책 (사용자 결정 2026-05-03)

- ✅ **A-1.** 기본 동작: `author=NULL` 유지. 모든 강사가 공용 풀로 사용.
- ✅ **A-3.** 옵션 동작: 학원장이 작성한 CSV로 일괄 author 부여.
- ❌ **A-4 금지:** "최근 보고서 작성자" 같은 자동 추론 귀속은 절대 사용하지 않는다. 추론으로 잘못된 강사에 자료가 묶이면 저작권 침해 위험.

## CSV 포맷

```csv
document_id,author_username
123,kang_su_hyun
148,park_chul
207,park_chul
```

- `document_id`: `MatchupDocument.id`
- `author_username`: `User.username` 외부 노출 형식 (즉 `t{tenant_id}_` prefix 제거된 값). `user_internal_username()`으로 내부 변환.
- 빈 author_username 또는 미존재 user → 해당 row skip + 로그.

## Management Command 시그니처

```bash
# Dry-run (default) — 실제 변경 없이 매핑 미리보기만
python manage.py assign_doc_authors \
  --tenant=2 \
  --csv=tools/T2_doc_author_map.csv

# 실제 적용
python manage.py assign_doc_authors \
  --tenant=2 \
  --csv=tools/T2_doc_author_map.csv \
  --apply

# 추가 옵션
  --overwrite     # 이미 author 있는 doc도 덮어쓰기 (default: skip)
  --report=path   # 결과 CSV 저장 (id, before_author, after_author, status)
```

## 안전 장치

1. **Tenant scope 강제:** `--tenant` 필수. 다른 tenant doc은 절대 안 건드림.
2. **Dry-run 기본:** `--apply` 명시 안 하면 미리보기만.
3. **User 검증:** username이 해당 tenant의 active membership 보유한 staff role(owner/admin/teacher/assistant)인지 확인. 학생/학부모/외부 user 차단.
4. **Overwrite 차단 default:** 이미 author 있는 doc은 skip. `--overwrite` 명시 시에만 변경.
5. **Audit log:** 모든 변경(매핑 적용/skip/실패)을 stdout + `--report` CSV에 기록.
6. **Trigger 분리:** 매치업 doc만 대상. inventory file / hit report 등은 영향 없음.

## 구현 위치

- `apps/domains/matchup/management/commands/assign_doc_authors.py` (신규)
- 의존: `apps.core.models.User` / `TenantMembership` / `apps.core.models.user.user_internal_username`
- 출력: stdout 표 + 선택 `--report` CSV

## 후속 작업 (옵션)

- 학원장 페이지에 CSV 업로드 UI 추가 (`AdminMatchupSettings` 페이지 검토)
- 매핑 결과 검수 UI: "이 자료는 누구 강사 자료로 분류됐나" 직접 보기
- doc 단위 author 변경 endpoint (`PATCH /matchup/documents/<id>/ {"author_id": ...}`) — admin only

## 변경 안전성

- `MatchupDocument.author` 변경 시 자동으로 `find_similar_problems`의 격리 결과가 바뀜. 기존 보고서가 다른 강사 자료로 채워졌더라도 그대로 유지(보고서는 `selected_problem_ids`로 problem id 참조하므로 author 변경 영향 없음).
- 변경 후 재분석/재인덱싱 불필요 — author 필드는 검색 필터에만 사용.

## 미구현 사유

학원장이 정책 결정 + CSV 작성 시점이 아직 미정. 도구 자체는 단순한 `UPDATE WHERE id IN (...)` 쿼리이므로 정책 결정 후 30분 내 구현 가능. 미리 구현해두면 "사용 안 하는 코드" 자산이 됨.
