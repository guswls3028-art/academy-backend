"""Lecture/Session 저장 시 ScopeNode 자동 생성."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.domains.lectures.models import Lecture, Session
from apps.domains.community.services.scope_node_service import (
    ensure_scope_node_for_lecture,
    ensure_scope_node_for_session,
)


@receiver(post_save, sender=Lecture)
def on_lecture_saved(sender, instance, created, **kwargs):
    if created:
        ensure_scope_node_for_lecture(instance)


@receiver(post_save, sender=Session)
def on_session_saved(sender, instance, created, **kwargs):
    if created:
        ensure_scope_node_for_session(instance)
