"""
results 도메인 — 시험(Exam) 결과와 집계 책임.

README.md가 현재 책임 경계와 변경 규칙의 기준이다.

책임 범위:
- ExamAttempt(append-only 시도 기록, is_representative 유일제약).
- ResultFact(append-only 원시 이벤트 로그).
- ResultItem(문항 단위 스냅샷).
- Result(대표 결과 스냅샷, FK = enrollment_id).
- ExamResult(legacy compatibility snapshot, submission OneToOne).
- aggregations/ (session/lecture/global 집계의 유일 장소).

❌ 본 도메인은 "통합 결과"가 아니다.
- 시험(Exam) 결과만 다룬다.
- 숙제(Homework) 결과는 homework_results.HomeworkScore 가 별도 보관.
- 클리닉/진척 등 cross-domain 합산이 필요하면 progress·clinic 도메인이 두 결과를 모두 read 한다.

❌ Result.student_id FK 금지. 항상 enrollment_id.
❌ 단순 View/Serializer/Model 안에서 집계 금지. 집계는 aggregations/ 또는 명시된 BFF view만.

평가 5도메인 책임 분담은 backend/docs/architecture/hexagonal-cutover-policy.md §8 참조.
"""
