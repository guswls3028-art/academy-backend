"""Homework-domain DB read helpers for cross-domain callers."""

from __future__ import annotations


def homework_target_info_map(homework_ids, tenant=None) -> dict[int, dict]:
    from apps.domains.homework_results.models import Homework

    info: dict[int, dict] = {}
    if not homework_ids:
        return info

    queryset = Homework.objects.filter(id__in=homework_ids)
    if tenant is not None:
        queryset = queryset.filter(tenant=tenant)

    for homework in queryset.select_related("session__lecture").order_by("id"):
        session = homework.session
        info[homework.id] = {
            "target_title": homework.title,
            "lecture_id": session.lecture_id if session else None,
            "lecture_title": session.lecture.title if session and session.lecture else "",
            "session_id": session.id if session else None,
        }
    return info
