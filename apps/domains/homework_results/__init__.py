"""
homework_results 도메인 — 과제 정의 + 점수 스냅샷.

⚠️ 도메인 이름이 오해를 유발한다.
- "results"라는 이름과 달리 본 도메인의 본체는 Homework(숙제 정의) 모델이다.
- HomeworkScore는 부가물(엔롤먼트×세션×과제 단위 점수 스냅샷).
- exams/results 의 "출제↔결과" 분리 패턴을 흉내 냈으나, 숙제는 question 단위가 없어
  Result/ResultFact/ResultItem 같은 정교한 분리가 불필요하다.

책임:
- Homework(세션 단위 과제 또는 템플릿).
- HomeworkScore(enrollment × session × homework × attempt_index 스냅샷,
  meta.status="NOT_SUBMITTED" 로 미제출 표현).

⚠️ 향후 homework 도메인으로 통합 예정 (옵션 B, multi-PR).
신규 코드는 homework 도메인과의 경계를 새로 만들지 않는다.

평가 5도메인 책임 분담은 backend/docs/00-SSOT/v1.1.1/HEXAGONAL-CUTOVER-POLICY.md §8 참조.
"""
