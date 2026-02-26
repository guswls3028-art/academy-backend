AdminExamResultDetail API
GET /results/admin/exams/{exam_id}/enrollments/{enrollment_id}/

Response
{
  "exam_id": 10,
  "enrollment_id": 99,
  "total_score": 85,
  "max_score": 100,

  "items": [
    {
      "question_id": 123,
      "answer": "B",
      "is_correct": true,
      "score": 1
    }
  ],

  "allow_retake": true,
  "max_attempts": 3,
  "can_retake": true,

  "clinic_required": false
}

주의사항 (CS용)

clinic_required=true 는 “보충 수업 대상”이 아니라
“위험 상태 플래그”

시험 합격 여부는 이 API에 없음
→ SessionProgress.exam_passed 사용