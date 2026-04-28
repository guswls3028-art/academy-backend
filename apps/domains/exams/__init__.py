"""
exams 도메인 — 출제(question authoring) SSOT.

책임:
- 시험(Exam) 정의·자산(Sheet/Question/AnswerKey/ExamAsset)·템플릿(TemplateBundle).
- ExamEnrollment(시험 단위 응시 자격).
- 출제 단위만 다룬다.

비책임 (다른 도메인 소유):
- 답안: submissions 도메인.
- 채점·결과·집계: results 도메인.
- 숙제(Homework): homework / homework_results 도메인.

평가 5도메인 책임 분담은 backend/docs/00-SSOT/v1.1.1/HEXAGONAL-CUTOVER-POLICY.md §8 참조.
"""
