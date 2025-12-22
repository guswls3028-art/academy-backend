from django.contrib import admin
from .models import Question, Answer


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "enrollment", "is_answered", "created_at")
    list_display_links = ("id", "title")
    list_filter = ("is_answered",)
    search_fields = ("title", "enrollment__student__name")
    ordering = ("-created_at",)


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ("id", "question", "created_at")
    list_display_links = ("id", "question")
    search_fields = ("question__title",)
    ordering = ("-created_at",)
