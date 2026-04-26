from django.contrib import admin
from .models import ScopeNode, PostEntity, PostMapping, PostReply, PostTemplate


@admin.register(ScopeNode)
class ScopeNodeAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "level", "lecture", "session", "parent")
    list_filter = ("tenant", "level")


@admin.register(PostEntity)
class PostEntityAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "title", "post_type", "created_by", "created_at")
    list_filter = ("tenant", "post_type")


@admin.register(PostMapping)
class PostMappingAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "node", "created_at")


@admin.register(PostReply)
class PostReplyAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "post", "created_by", "created_at")


@admin.register(PostTemplate)
class PostTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "name", "order", "updated_at")
    list_filter = ("tenant",)
