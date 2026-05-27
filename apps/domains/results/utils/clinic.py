# apps/domains/results/utils/clinic.py
from __future__ import annotations

from typing import Any, Iterable, Set

from django.apps import apps

from apps.domains.lectures.models import Session
from apps.domains.progress.models import ClinicLink, SessionProgress


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _link_source_id(link: ClinicLink, source_type: str) -> int | None:
    meta = link.meta if isinstance(getattr(link, "meta", None), dict) else {}
    if getattr(link, "source_type", None) == source_type:
        return _int_or_none(
            getattr(link, "source_id", None) or meta.get(f"{source_type}_id")
        )
    if getattr(link, "source_type", None) is None:
        return _int_or_none(meta.get(f"{source_type}_id"))
    return None


def filter_live_source_links(
    links: Iterable[ClinicLink],
    *,
    tenant: Any,
) -> list[ClinicLink]:
    """
    ClinicLink read-side guard.

    운영 노출의 SSOT는 unresolved ClinicLink지만, 원본 시험/과제가 이미 차시에서
    제거된 링크는 SOURCE_REMOVED 전이가 누락돼도 화면/통계에서 fail closed 한다.
    """
    links_list = list(links)
    if not links_list or tenant is None:
        return []

    session_ids = {
        int(getattr(link, "session_id", 0) or 0)
        for link in links_list
        if getattr(link, "session_id", None)
    }
    exam_ids = {
        exam_id
        for link in links_list
        for exam_id in [_link_source_id(link, "exam")]
        if exam_id is not None
    }
    homework_ids = {
        homework_id
        for link in links_list
        for homework_id in [_link_source_id(link, "homework")]
        if homework_id is not None
    }

    live_exam_pairs: set[tuple[int, int]] = set()
    if exam_ids and session_ids:
        Exam = apps.get_model("exams", "Exam")
        live_exam_pairs = {
            (int(exam_id), int(session_id))
            for exam_id, session_id in Exam.objects.filter(
                tenant=tenant,
                exam_type="regular",
                is_active=True,
                id__in=exam_ids,
                sessions__id__in=session_ids,
            ).values_list("id", "sessions__id")
        }

    live_homework_pairs: set[tuple[int, int]] = set()
    live_homework_assignment_triples: set[tuple[int, int, int]] = set()
    if homework_ids and session_ids:
        Homework = apps.get_model("homework_results", "Homework")
        live_homework_pairs = {
            (int(homework_id), int(session_id))
            for homework_id, session_id in Homework.objects.filter(
                tenant=tenant,
                homework_type="regular",
                id__in=homework_ids,
                session_id__in=session_ids,
            )
            .exclude(meta__removed_from_session_at__isnull=False)
            .values_list("id", "session_id")
        }
        HomeworkAssignment = apps.get_model("homework", "HomeworkAssignment")
        live_homework_assignment_triples = {
            (int(homework_id), int(session_id), int(enrollment_id))
            for homework_id, session_id, enrollment_id in HomeworkAssignment.objects.filter(
                tenant=tenant,
                homework_id__in=homework_ids,
                session_id__in=session_ids,
            ).values_list("homework_id", "session_id", "enrollment_id")
        }

    live_links: list[ClinicLink] = []
    for link in links_list:
        source_type = getattr(link, "source_type", None)
        session_id = int(getattr(link, "session_id", 0) or 0)

        exam_id = _link_source_id(link, "exam")
        if exam_id is not None:
            if (exam_id, session_id) in live_exam_pairs:
                live_links.append(link)
            continue

        homework_id = _link_source_id(link, "homework")
        if homework_id is not None:
            enrollment_id = int(getattr(link, "enrollment_id", 0) or 0)
            if (
                (homework_id, session_id) in live_homework_pairs
                and (homework_id, session_id, enrollment_id)
                in live_homework_assignment_triples
            ):
                live_links.append(link)
            continue

        # Ambiguous legacy automatic links without source metadata remain visible.
        if source_type is None:
            live_links.append(link)

    return live_links


def get_clinic_enrollment_ids_for_session(
    *,
    session: Session,
    include_manual: bool = False,
    exclude_completed: bool = True,
) -> Set[int]:
    """
    ✅ Clinic 단일 규칙 제공

    기본 정책(권장/안전):
    - 운영에서 clinic_required/clinic_rate는 '자동 트리거' 기준으로 통일한다.
      -> include_manual=False (default)

    왜냐하면:
    - 수동 클리닉(강사 추천/요청)은 UX/운영 정책에 따라 케이스가 달라서
      통계에 섞이면 화면마다 "왜 다르냐" 문제가 반복된다.

    필요하면 include_manual=True로
    수동까지 포함한 '전체 clinic 대상'을 만들 수 있다.

    tenant 격리: session FK는 이미 tenant-scoped이지만,
    ClinicLink.tenant FK로 명시 필터하여 암묵적 의존 제거.
    """
    # tenant 격리: session.lecture.tenant로 명시 필터
    tenant_id = getattr(getattr(session, "lecture", None), "tenant_id", None)
    qs = ClinicLink.objects.filter(session=session)
    if tenant_id is not None:
        qs = qs.filter(tenant_id=tenant_id)

    qs = qs.filter(resolved_at__isnull=True)

    if not include_manual:
        qs = qs.filter(is_auto=True)

    qs = qs.filter(enrollment__status="ACTIVE")

    links = filter_live_source_links(
        qs.order_by("id"),
        tenant=getattr(getattr(session, "lecture", None), "tenant", None),
    )
    ids = {
        int(getattr(link, "enrollment_id", 0) or 0)
        for link in links
        if getattr(link, "enrollment_id", None)
    }
    if not exclude_completed or not ids:
        return ids

    completed_ids = set(
        SessionProgress.objects.filter(
            session=session,
            enrollment_id__in=ids,
            completed=True,
        ).values_list("enrollment_id", flat=True)
    )
    return ids - {int(enrollment_id) for enrollment_id in completed_ids}


def is_clinic_required(
    *,
    session: Session,
    enrollment_id: int,
    include_manual: bool = False,
) -> bool:
    """
    ✅ enrollment 단위 clinic 여부 (단일 진실)
    """
    enrollment_id = int(enrollment_id)
    ids = get_clinic_enrollment_ids_for_session(
        session=session,
        include_manual=include_manual,
    )
    return enrollment_id in ids
