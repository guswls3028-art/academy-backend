from django.db import models

from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture


# ========================================================
# Board Category
# ========================================================

class BoardCategory(models.Model):
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="board_categories",
    )
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"[{self.lecture.title}] {self.name}"


# ========================================================
# Board Post
# ========================================================

class BoardPost(models.Model):
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="board_posts",
    )
    category = models.ForeignKey(
        BoardCategory,
        on_delete=models.CASCADE,
        related_name="posts",
    )

    title = models.CharField(max_length=255)
    content = models.TextField()
    created_by = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


# ========================================================
# Board Attachment
# ========================================================

class BoardAttachment(models.Model):
    post = models.ForeignKey(
        BoardPost,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to="board/%Y/%m/%d/")


# ========================================================
# Board Read Status
# ========================================================

class BoardReadStatus(models.Model):
    post = models.ForeignKey(
        BoardPost,
        on_delete=models.CASCADE,
        related_name="read_status",
    )
    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.CASCADE,
    )
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("post", "enrollment")
