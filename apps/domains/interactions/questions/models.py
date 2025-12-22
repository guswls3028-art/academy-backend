from django.db import models


# ========================================================
# Question / Answer
# ========================================================

class Question(models.Model):
    """
    학생이 수강 중 남기는 질문.
    """

    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.CASCADE,
        related_name="questions",
    )

    title = models.CharField(max_length=200)
    content = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    is_answered = models.BooleanField(default=False)

    def __str__(self):
        return self.title


class Answer(models.Model):
    """
    Question에 대한 단일 답변.
    """

    question = models.OneToOneField(
        Question,
        on_delete=models.CASCADE,
        related_name="answer",
    )

    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Answer to {self.question.title}"
