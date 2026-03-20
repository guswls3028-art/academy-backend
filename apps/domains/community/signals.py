"""Lecture/Session 저장 시 ScopeNode 자동 생성.

✅ V1.1.1: created=True 제한 제거.
get_or_create이므로 매번 호출해도 안전하며,
기존 Lecture/Session에 ScopeNode가 누락된 경우를 자동 복구한다.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.domains.lectures.models import Lecture, Session
from apps.domains.community.services.scope_node_service import (
    ensure_scope_node_for_lecture,
    ensure_scope_node_for_session,
)


@receiver(post_save, sender=Lecture)
def on_lecture_saved(sender, instance, **kwargs):
    ensure_scope_node_for_lecture(instance)


@receiver(post_save, sender=Session)
def on_session_saved(sender, instance, **kwargs):
    ensure_scope_node_for_session(instance)
