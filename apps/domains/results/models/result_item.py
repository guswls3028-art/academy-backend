from django.db import models
from apps.api.common.models import BaseModel


class ResultItem(BaseModel):
    """
    문항별 최신 결과 상태 (snapshot)
    """

    result = models.ForeignKey(
        "results.Result",
        on_delete=models.CASCADE,
        related_name="items",
    )

    question = models.ForeignKey(
        "exams.ExamQuestion",
        on_delete=models.CASCADE,
        db_column="question_id",
        related_name="result_items",
    )

    answer = models.TextField(blank=True)
    is_correct = models.BooleanField(default=False)
    include_in_wrong_note = models.BooleanField(
        default=False,
        help_text=(
            "정오 여부와 무관하게 복습/오답노트에 포함할지 여부. "
            "예: Ymath 엑셀의 숫자 0은 정답이면서 이 값이 true다."
        ),
    )

    score = models.FloatField(default=0.0)
    max_score = models.FloatField(default=0.0)

    source = models.CharField(max_length=20)

    class Meta:
        db_table = "results_result_item"
        unique_together = ("result", "question")
