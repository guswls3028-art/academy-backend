"""Tenant scoped persistence of session score-table composition."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, ValidationError

from apps.domains.lectures.models import Session
from apps.domains.homework_results.models import Homework
from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.domains.results.services.assessment_totals import total_config


class CompositionConflict(APIException):
    status_code = 409
    default_detail = "다른 선생님이 합산 설정을 변경했습니다. 새 설정을 확인하고 다시 저장해 주세요."


def assessment_options(session, tenant):
    exams = [{"exam_id": ex.id, "title": ex.title, "max_score": ex.max_score}
             for ex in get_exams_for_session(session).filter(tenant=tenant)]
    homeworks = [{"homework_id": hw.id, "title": hw.title, "max_score": hw.default_max_score,
                  "grading_mode": hw.grading_mode}
                 for hw in Homework.objects.filter(session=session, tenant=tenant, homework_type="regular")
                 .exclude(meta__removed_from_session_at__isnull=False)
                 .order_by("display_order", "created_at", "id")]
    return exams, homeworks


def read_total_config(*, session_id, tenant):
    session = get_object_or_404(Session, pk=session_id, lecture__tenant=tenant)
    exams, homeworks = assessment_options(session, tenant)
    return total_config(session, exams, homeworks)


@transaction.atomic
def save_total_config(*, session_id, tenant, payload):
    session = get_object_or_404(Session.objects.select_for_update(), pk=session_id, lecture__tenant=tenant)
    exams, homeworks = assessment_options(session, tenant)
    current = total_config(session, exams, homeworks)
    if payload["expected_version"] != current["version"]:
        raise CompositionConflict()
    for key, allowed in (("exam_ids", {row["exam_id"] for row in exams}),
                         ("homework_ids", {row["homework_id"] for row in homeworks if row["grading_mode"] != "COMPLETION"})):
        values = payload[key]
        if len(values) != len(set(values)) or not set(values).issubset(allowed):
            raise ValidationError({key: "이 차시에 속한 점수형 평가를 중복 없이 선택해 주세요."})
    session.score_totals = {"version": current["version"] + 1,
                            "exam_ids": payload["exam_ids"], "homework_ids": payload["homework_ids"]}
    for key, allowed in (
        ("exam_order", {row["exam_id"] for row in exams}),
        ("homework_order", {row["homework_id"] for row in homeworks}),
    ):
        if key in payload:
            values = payload[key]
            if len(values) != len(set(values)) or set(values) != allowed:
                raise ValidationError({key: "차시 평가 목록이 변경되었습니다. 새 목록을 확인해 주세요."})
            session.score_totals[key] = values
        else:
            session.score_totals[key] = current[key]
    session.save(update_fields=["score_totals", "updated_at"])
    return total_config(session, exams, homeworks)


def student_session_totals(*, tenant, exam_rows, homework_rows, absent_learning_pairs=frozenset()):
    """Summarize the same first-attempt values already authorized for this child."""
    from apps.domains.results.services.assessment_totals import row_totals
    from apps.support.progress.session_calculator_dependencies import get_exam_target_enrollment_pairs_for_session
    from apps.domains.homework.models import HomeworkAssignment

    pairs = {(int(row["session_id"]), int(row["enrollment_id"]))
             for row in [*exam_rows, *homework_rows] if row.get("session_id") is not None}
    sessions = Session.objects.filter(id__in={pair[0] for pair in pairs}, lecture__tenant=tenant).select_related("lecture").order_by("-order", "-id")
    exam_lookup = {(row["enrollment_id"], row["exam_id"]): row for row in exam_rows}
    homework_targets = set(HomeworkAssignment.objects.filter(
        tenant=tenant, session_id__in={pair[0] for pair in pairs},
        enrollment_id__in={pair[1] for pair in pairs}, enrollment__tenant=tenant,
        homework__tenant=tenant,
    ).values_list("session_id", "enrollment_id", "homework_id"))
    summaries = []
    for session in sessions:
        exams, homeworks = assessment_options(session, tenant)
        config = total_config(session, exams, homeworks)
        enrollment_ids = [eid for sid, eid in pairs if sid == session.id]
        targets = get_exam_target_enrollment_pairs_for_session(session=session, enrollment_ids=enrollment_ids)
        meta = {"exams": exams, "homeworks": homeworks, "total_config": config}
        for enrollment_id in enrollment_ids:
            exam_entries = []
            for exam in exams:
                result = exam_lookup.get((enrollment_id, exam["exam_id"]))
                if (enrollment_id, exam["exam_id"]) not in targets:
                    continue
                if result is None and (enrollment_id, session.id) in absent_learning_pairs:
                    continue
                exam_entries.append({"exam_id": exam["exam_id"], "block": {
                    "score": result.get("total_score") if result else None,
                    "grading_status": result.get("grading_status") if result else None,
                    "is_provisional": result.get("is_provisional", False) if result else False,
                    "meta": {"status": result.get("meta_status")} if result else {},
                }})
            homework_entries = [{"homework_id": row["homework_id"], "block": {
                "score": row.get("score"),
                "meta": {"status": "NOT_SUBMITTED" if row.get("submission_state") == "NOT_SUBMITTED" else ""},
            }} for row in homework_rows
                if row.get("session_id") == session.id and row["enrollment_id"] == enrollment_id
                and (session.id, enrollment_id, row["homework_id"]) in homework_targets]
            summaries.append({
                "session_id": session.id, "lecture_id": session.lecture_id,
                "session_title": session.title or session.display_label, "lecture_title": session.lecture.title,
                **row_totals(meta, {"exams": exam_entries, "homeworks": homework_entries}),
            })
    return summaries
