"""
Read-only assessment lifecycle drift report.

Business SSOT:
- live exam = regular + is_active + linked to session
- live homework = regular + session + not removed_from_session_at
- clinic target = unresolved automatic ClinicLink whose source is live
"""
from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Read-only drift report for assessment lifecycle state."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, default=None)
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--sample", type=int, default=10)

    def handle(self, *args, **options):
        from apps.domains.exams.models import Exam
        from apps.domains.homework.models import HomeworkAssignment
        from apps.domains.homework_results.models import Homework
        from apps.domains.progress.models import ClinicLink

        tenant_id = options.get("tenant")
        sample_size = max(1, int(options.get("sample") or 10))

        exam_qs = Exam.objects.all()
        homework_qs = Homework.objects.all()
        link_qs = ClinicLink.objects.filter(is_auto=True, resolved_at__isnull=True)
        if tenant_id is not None:
            exam_qs = exam_qs.filter(tenant_id=tenant_id)
            homework_qs = homework_qs.filter(tenant_id=tenant_id)
            link_qs = link_qs.filter(tenant_id=tenant_id)

        inactive_linked_exams = exam_qs.filter(
            exam_type=Exam.ExamType.REGULAR,
            is_active=False,
            sessions__isnull=False,
        ).distinct()
        template_linked_exams = exam_qs.filter(
            exam_type=Exam.ExamType.TEMPLATE,
            sessions__isnull=False,
        ).distinct()
        active_linked_closed_exams = exam_qs.filter(
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
            status=Exam.Status.CLOSED,
            sessions__isnull=False,
        ).distinct()
        removed_homework_with_assignments = homework_qs.exclude(
            meta__removed_from_session_at__isnull=False
        ).none()
        removed_homework_ids = list(
            homework_qs.exclude(meta__removed_from_session_at__isnull=True)
            .values_list("id", flat=True)
        )
        if removed_homework_ids:
            assignment_homework_ids = set(
                HomeworkAssignment.objects.filter(
                    homework_id__in=removed_homework_ids
                ).values_list("homework_id", flat=True)
            )
            removed_homework_with_assignments = homework_qs.filter(
                id__in=assignment_homework_ids
            )

        live_exam_pairs = {
            (int(exam_id), int(session_id))
            for exam_id, session_id in exam_qs.filter(
                exam_type=Exam.ExamType.REGULAR,
                is_active=True,
                sessions__isnull=False,
            ).values_list("id", "sessions__id")
        }
        live_homework_pairs = {
            (int(homework_id), int(session_id))
            for homework_id, session_id in homework_qs.filter(
                homework_type=Homework.HomeworkType.REGULAR,
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

        ghost_links: list[dict[str, Any]] = []
        for link in link_qs.only(
            "id",
            "tenant_id",
            "session_id",
            "enrollment_id",
            "source_type",
            "source_id",
            "meta",
        ).iterator(chunk_size=500):
            exam_id = source_id(link, "exam")
            if exam_id is not None:
                if (exam_id, int(link.session_id)) not in live_exam_pairs:
                    ghost_links.append({
                        "id": int(link.id),
                        "tenant_id": int(link.tenant_id),
                        "session_id": int(link.session_id),
                        "enrollment_id": int(link.enrollment_id),
                        "source_type": "exam",
                        "source_id": exam_id,
                    })
                continue

            homework_id = source_id(link, "homework")
            if homework_id is not None:
                triple = (homework_id, int(link.session_id), int(link.enrollment_id))
                if (
                    (homework_id, int(link.session_id)) not in live_homework_pairs
                    or triple not in live_homework_assignments
                ):
                    ghost_links.append({
                        "id": int(link.id),
                        "tenant_id": int(link.tenant_id),
                        "session_id": int(link.session_id),
                        "enrollment_id": int(link.enrollment_id),
                        "source_type": "homework",
                        "source_id": homework_id,
                    })

        report = {
            "tenant": tenant_id if tenant_id is not None else "all",
            "inactive_regular_linked_exam_count": inactive_linked_exams.count(),
            "template_linked_exam_count": template_linked_exams.count(),
            "active_regular_linked_closed_exam_count": active_linked_closed_exams.count(),
            "removed_homework_with_assignment_count": removed_homework_with_assignments.count(),
            "unresolved_non_live_source_clinic_link_count": len(ghost_links),
            "samples": {
                "inactive_regular_linked_exam_ids": list(
                    inactive_linked_exams.values_list("id", flat=True)[:sample_size]
                ),
                "template_linked_exam_ids": list(
                    template_linked_exams.values_list("id", flat=True)[:sample_size]
                ),
                "active_regular_linked_closed_exam_ids": list(
                    active_linked_closed_exams.values_list("id", flat=True)[:sample_size]
                ),
                "removed_homework_with_assignment_ids": list(
                    removed_homework_with_assignments.values_list("id", flat=True)[:sample_size]
                ),
                "unresolved_non_live_source_clinic_links": ghost_links[:sample_size],
            },
        }

        if options.get("as_json"):
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
            return

        self.stdout.write("Assessment lifecycle drift report")
        self.stdout.write(f"tenant={report['tenant']}")
        for key, value in report.items():
            if key in {"tenant", "samples"}:
                continue
            self.stdout.write(f"{key}={value}")
        self.stdout.write("samples=" + json.dumps(report["samples"], ensure_ascii=False))
        self.stdout.write("Report complete. No data was modified.")
