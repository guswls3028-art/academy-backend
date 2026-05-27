"""
Repair assessment lifecycle drift.

Default mode is dry-run. Use --apply to mutate data.
"""
from __future__ import annotations

import json
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = "Repair stale assessment lifecycle state. Dry-run unless --apply is provided."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, default=None)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--sample", type=int, default=10)

    def handle(self, *args, **options):
        Exam = apps.get_model("exams", "Exam")
        HomeworkAssignment = apps.get_model("homework", "HomeworkAssignment")
        Homework = apps.get_model("homework_results", "Homework")
        ClinicLink = apps.get_model("progress", "ClinicLink")

        tenant_id = options.get("tenant")
        apply = bool(options.get("apply"))
        sample_size = max(1, int(options.get("sample") or 10))

        exam_qs = Exam.objects.all()
        homework_qs = Homework.objects.all()
        link_qs = ClinicLink.objects.filter(is_auto=True, resolved_at__isnull=True)
        if tenant_id is not None:
            exam_qs = exam_qs.filter(tenant_id=tenant_id)
            homework_qs = homework_qs.filter(tenant_id=tenant_id)
            link_qs = link_qs.filter(tenant_id=tenant_id)

        inactive_linked_exams = list(
            exam_qs.filter(
                exam_type="regular",
                is_active=False,
                sessions__isnull=False,
            ).distinct().prefetch_related("sessions")
        )
        template_linked_exams = list(
            exam_qs.filter(
                exam_type="template",
                sessions__isnull=False,
            ).distinct().prefetch_related("sessions")
        )

        detached_pairs: list[dict[str, int]] = []
        for exam in [*inactive_linked_exams, *template_linked_exams]:
            for session_id in exam.sessions.values_list("id", flat=True):
                detached_pairs.append({
                    "exam_id": int(exam.id),
                    "session_id": int(session_id),
                    "tenant_id": int(exam.tenant_id),
                })

        live_exam_pairs = {
            (int(exam_id), int(session_id))
            for exam_id, session_id in exam_qs.filter(
                exam_type="regular",
                is_active=True,
                sessions__isnull=False,
            ).values_list("id", "sessions__id")
        }
        live_homework_pairs = {
            (int(homework_id), int(session_id))
            for homework_id, session_id in homework_qs.filter(
                homework_type="regular",
                session__isnull=False,
            )
            .exclude(meta__removed_from_session_at__isnull=False)
            .values_list("id", "session_id")
        }
        live_homework_assignments = {
            (int(homework_id), int(session_id), int(enrollment_id))
            for homework_id, session_id, enrollment_id in HomeworkAssignment.objects.filter(
                homework_id__in=[homework_id for homework_id, _ in live_homework_pairs]
            ).values_list("homework_id", "session_id", "enrollment_id")
        }

        def source_id(link: Any, source_type: str) -> int | None:
            meta = link.meta if isinstance(getattr(link, "meta", None), dict) else {}
            if link.source_type == source_type:
                raw = link.source_id or meta.get(f"{source_type}_id")
            elif link.source_type is None:
                raw = meta.get(f"{source_type}_id")
            else:
                raw = None
            try:
                return int(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None

        ghost_links = []
        for link in link_qs.only(
            "id",
            "tenant_id",
            "session_id",
            "enrollment_id",
            "source_type",
            "source_id",
            "meta",
            "resolution_history",
        ).iterator(chunk_size=500):
            exam_id = source_id(link, "exam")
            if exam_id is not None:
                if (exam_id, int(link.session_id)) not in live_exam_pairs:
                    ghost_links.append((link, "exam", exam_id))
                continue

            homework_id = source_id(link, "homework")
            if homework_id is not None:
                triple = (homework_id, int(link.session_id), int(link.enrollment_id))
                if (
                    (homework_id, int(link.session_id)) not in live_homework_pairs
                    or triple not in live_homework_assignments
                ):
                    ghost_links.append((link, "homework", homework_id))

        resolved_link_ids: list[int] = []
        if apply:
            with transaction.atomic():
                for exam in [*inactive_linked_exams, *template_linked_exams]:
                    session_ids = list(exam.sessions.values_list("id", flat=True))
                    if session_ids:
                        exam.sessions.remove(*session_ids)

                now = timezone.now()
                for link, source_type, source_id_value in ghost_links:
                    evidence = {
                        "repair": "assessment_state_drift",
                        "source_type": source_type,
                        "source_id": int(source_id_value),
                    }
                    history = list(link.resolution_history or [])
                    history.append({
                        "at": now.isoformat(),
                        "action": "resolve",
                        "resolution_type": "SOURCE_REMOVED",
                        "evidence": evidence,
                    })
                    link.resolved_at = now
                    link.resolution_type = "SOURCE_REMOVED"
                    link.resolution_evidence = evidence
                    link.resolution_history = history
                    link.save(update_fields=[
                        "resolved_at",
                        "resolution_type",
                        "resolution_evidence",
                        "resolution_history",
                        "updated_at",
                    ])
                    resolved_link_ids.append(int(link.id))

        report = {
            "tenant": tenant_id if tenant_id is not None else "all",
            "mode": "apply" if apply else "dry-run",
            "detachable_exam_session_pair_count": len(detached_pairs),
            "resolved_non_live_source_clinic_link_count": len(resolved_link_ids) if apply else len(ghost_links),
            "samples": {
                "detachable_exam_session_pairs": detached_pairs[:sample_size],
                "non_live_source_clinic_link_ids": [
                    int(link.id) for link, _, _ in ghost_links[:sample_size]
                ],
                "resolved_non_live_source_clinic_link_ids": resolved_link_ids[:sample_size],
            },
        }

        if options.get("as_json"):
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
            return

        self.stdout.write("Assessment lifecycle repair report")
        self.stdout.write(f"tenant={report['tenant']}")
        self.stdout.write(f"mode={report['mode']}")
        self.stdout.write(
            f"detachable_exam_session_pair_count={report['detachable_exam_session_pair_count']}"
        )
        self.stdout.write(
            "resolved_non_live_source_clinic_link_count="
            f"{report['resolved_non_live_source_clinic_link_count']}"
        )
        self.stdout.write("samples=" + json.dumps(report["samples"], ensure_ascii=False))
