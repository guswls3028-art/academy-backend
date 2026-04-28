"""
results 도메인 — 시험(Exam) 결과 SSOT + Aggregation 단일 책임지.

⚠️ 본 도메인은 SEALED. 재설계·책임 이동 금지. README.md 봉인 문서 참조.

책임 범위:
- ExamAttempt(append-only 시도 기록, is_representative 유일제약).
- ResultFact(append-only 원시 이벤트 로그).
- ResultItem(문항 단위 스냅샷).
- Result(대표 결과 스냅샷, FK = enrollment_id).
- ExamResult(legacy 채점 컨테이너, submission OneToOne).
- aggregations/ (session/lecture/global 집계의 유일 장소).

❌ 본 도메인은 "통합 결과"가 아니다.
- 시험(Exam) 결과만 다룬다.
- 숙제(Homework) 결과는 homework_results.HomeworkScore 가 별도 보관.
- 클리닉/진척 등 cross-domain 합산이 필요하면 progress·clinic 도메인이 두 결과를 모두 read 한다.

❌ Result.student_id FK 금지. 항상 enrollment_id.
❌ View/Serializer/Model 안에서 집계 금지. 집계는 aggregations/ 만.

평가 5도메인 책임 분담은 backend/docs/00-SSOT/v1.1.1/HEXAGONAL-CUTOVER-POLICY.md §8 참조.
"""
