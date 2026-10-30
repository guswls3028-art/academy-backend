# Excel 가져오기·내보내기 운영

현재 Excel 경계와 장애 대응의 진입점이다. 권한·학생 식별·성적 갱신 정책은
각 도메인 정본에 두며, 운영 개인정보를 복사해 재현하지 않는다.

| 경로 | 실행 소유자와 검증 |
|---|---|
| 학생 일괄 등록, 강의/차시 기존 학생 등록 | `ExcelParsingService`, student import, enrollment services. [학생 정본](../domain/student-core.md). 관계 표기, 원본 행 번호, 부분 오류, 중복·재시도, 계정 보존·테넌트 검사 |
| 시험 정오표 양식·가져오기, 오답·분석 Excel | `results/services/exam_result_excel_import.py`, `exam_analysis_export.py`. [채점 정본](../domain/exam-grading.md). 미리보기 후 반영, 성적 편집 잠금, 전체 거래 롤백, 정수 ID, 출력 수식 차단 |
| 강의 출결 다운로드 | `attendance/utils/excel.py`, `excel_export_handler.py`. [출결 정본](../domain/attendance.md). 등록 명단·차시 상태·문자열 셀·동일 테넌트·서명 URL |
| 직원 급여 다운로드 | `repositories_staffs.py`, `excel_export_handler.py`. [직원 운영](../domain/staff-operations.md). 고정 snapshot 집합/revision, 공제·환급·반올림, 문자열 이름/계정 |
| 자료실·공개 자료 Excel 보기 | `resource_document_renderer.py`, public resource reader. 격리 Office 변환, 활성/외부 콘텐츠 거부, 시간·파일·메모리 제한. 등록/성적 쓰기와 분리 |
| 브라우저 생성 명단·템플릿·기타 다운로드 | frontend shared Excel reader/export와 각 feature 소유. 브라우저 정본에서 inventory·화면 검증 관리; 서버 정책을 복제하지 않음 |

## 입력과 자원 경계

HTTP 업로드와 worker는 `apps/shared/utils/excel_workbook.py`의 archive 검사를
공유한다. 압축 업로드 전체를 한 번 더 복사하지 않고 빌린 stream의 위치를 복원한다.
10 MiB 파일, 2,000 ZIP member, 100 MiB 전체 해제 크기가 기본 한도이며 성적
가져오기는 기존 50 MiB 해제 한도를 유지한다. 중복 member, 암호화, CRC 손상,
XML DOCTYPE는 실패한다. 확장자·MIME 검사도 유지한다.

학생 파서는 sheet별 sample로 선택한 뒤 한 sheet만 materialize한다. 실제 셀 기준
한도를 넘으면 저장 전에 실패한다. 성적 파서도 실제 셀을 bounded scan하여 허위·누락
dimension을 신뢰하지 않으며, 50 sheet/2,000행/600열 한도와 모든 반환·오류 경로의
archive close를 보장한다. 성적 수강등록ID는 양의 정수만 허용하며 소수점이나
지수 표기를 잘라 쓰지 않는다. 잘못된 연락처를 숫자만 추출하거나 이름만으로
대체하여 다른 학생의 성적에 연결하지 않는다.

## 장애 확인과 복구

1. 동일 테넌트의 job ID, job type, 현재 DB 상태와 단계 진행률을 확인한다.
   브라우저 연결 끊김은 작업 실패의 증거가 아니다. 먼저 기존 작업을 재조회한다.
   Redis 장애는 진행률 표시만 저하시키며 등록 거래를 실패시키지 않는다.
2. `DONE`이면 성공·중복·복원·제외·실패 행과 전체 건수를 대조한다. 이전 버전의
   count-only 결과에 상세 행이 없다고 실패 0으로 표현하지 않는다. 저장 결과는
   명단/수강/성적 화면의 재조회와 후속 역할에서도 확인한다.
3. 행 오류는 원본 행을 수정해 재업로드한다. 하나의 셀에 두 연락처가 있으면
   담당자가 정확한 보호자 연락처를 선택한다. 실패한 작업의 원본을 운영 DB에서
   임의 수정하거나 다른 테넌트로 복사하지 않는다.
4. 중단·claim 만료는 기존 worker claim fence/atomic completion을 따른다.
   이전 실행은 새 실행의 source를 삭제하거나 부분 저장을 완료 처리할 수 없다.
   정상 완료/현재 claim 실패의 정확한 source만 정리하고 임시 로컬 파일은 항상 닫는다.
5. R2/DB/queue 등 예상 밖 오류는 job ID로 내부 로그와 연결해 원인을 확인한다.
   민감한 원문·credential·서명 URL을 공유 로그나 결과 영속 저장에 넣지 않는다.
   재시도 전에 해당 상태를 확인하며 사용자 계정·비밀번호·수동 성적은 보존한다.

## 검증과 릴리스

전화번호, archive, worksheet 한도·dimension, 국제 학생명 오류, 진행률, 학생
계정 보존, 수강 중복·제외 행, 성적 정수 ID·archive 종료·원자적 반영 회귀를
실행한다. `test_excel_worker_claim_fence.py`의 동시성 증거는 PostgreSQL CI에서
검증한다. 출결/급여/시험 출력은 기존 도메인 테스트도 포함한다.

정식 development smoke는 합성 관계표기/전각 연락처와 소수점 오류를 동일 후보
API/Tools 런타임에서 검사한다. 계정과 실제 브라우저의 변경 경로는 격리 개발
QA에서 action → persistence → reload → downstream 및 desktop/390px를 검증하고
tenant/user cleanup zero를 확인한다. production 배포와 최종 readback은
[deployment modes](deployment-modes.md)와
[교차 저장소 release bundle](change-risk-and-release-bundle.md)을 따른다.
테스트 통과나 벤치마크를 실제 배포 완료로 기록하지 않는다.
