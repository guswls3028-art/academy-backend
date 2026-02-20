# 프론트·인프라 계약 및 백엔드 연동 사실 — SSOT_0217 (Cursor 참조용)

추측 금지. 프론트/인프라 구현 상세는 **academyfront** 쪽 **SSOT_0217** 의 `06-implemented-features.md` 참조. (동일 날짜 스냅샷에 있음.)

---

## 1. 프론트·인프라 요약 (상세는 academyfront SSOT_0217)

- **멀티테넌트 도메인**: hakwonplus.com, tchul.com, limglish.kr, ymath.co.kr (루트·www). 프론트 HOSTNAME_TO_TENANT_CODE = tchul, limglish, ymath. X-Tenant-Code는 axios에서 getTenantCodeForApiRequest()로 hostname → path → sessionStorage 순.
- **로그인 URL**: 테넌트 도메인에서는 항상 `도메인/login` 만 노출. `/login/limglish` 등은 해당 도메인에서 /login 으로 리다이렉트.
- **www → 루트**: Cloudflare Redirect Rules 301. 백엔드 설정 아님.
- **CORS**: 새 프론트 도메인 사용 시 academy CORS_ALLOWED_ORIGINS, CSRF_TRUSTED_ORIGINS 에 해당 오리진 추가 필요.

---

## 2. 백엔드 — 학생 엑셀 파싱

**파일**: `src/application/services/excel_parsing_service.py`

- **ExcelParsingService.run()**: payload에 file_key, tenant_id, lecture_id, initial_password 필요. R2에서 파일 다운로드 후 `parse_student_excel_file(local_path)` 호출.
- **에러**: `parse_student_excel_file` 결과 rows 비어 있으면 `raise ValueError("등록할 학생 데이터가 없습니다.")`. 이 메시지는 프론트에서도 동일 문자열로 alert/feedback 사용 (StudentCreateModal, LectureEnrollExcelModal).
- **용도**: 강의 수강생 일괄 등록 (lecture_enroll_from_excel_rows). 학생 "단독" 일괄 등록은 프론트에서 청크로 학생 생성 API 호출하며, 백엔드 이 서비스는 강의별 엑셀 업로드 job 용.

---

## 3. 참조

- 프론트 구현 사실 전부: academyfront **docs/SSOT_0217/cursor_only/06-implemented-features.md**
- 백엔드 core·API·배포: 본 폴더 cursor_01~05, docs/배포.md
