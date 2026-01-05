# apps/domains/results/urls.py
from django.urls import path

from apps.domains.results.views.exam_result_view import (
    ExamStatsView,
    ExamQuestionStatsView,
)
from apps.domains.results.views.wrong_note_view import WrongNoteView

# ✅ 추가
from apps.domains.results.views.student_exam_result_view import MyExamResultView

urlpatterns = [
    # 시험 통계
    path(
        "exams/<int:exam_id>/stats",
        ExamStatsView.as_view(),
        name="exam-stats",
    ),
    path(
        "exams/<int:exam_id>/questions/stats",
        ExamQuestionStatsView.as_view(),
        name="exam-question-stats",
    ),

    # 오답노트
    path(
        "wrong-notes",
        WrongNoteView.as_view(),
        name="wrong-note",
    ),

    # ✅ 학생 본인 성적 조회 (1개 API)
    path(
        "me/exams/<int:exam_id>/", 
        MyExamResultView.as_view(), 
        name="my-exam-result"
    ),
]
