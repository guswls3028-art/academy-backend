"""Scored OMR corrections must move one paper, not duplicate a student's grade."""

import pytest
from datetime import timedelta
from django.apps import apps
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.domains.results.models import ExamAttempt, ExamResult, Result, ResultFact, ResultItem
from apps.domains.results.services.grading_service import grade_submission
from apps.domains.results.services.attempt_service import ExamAttemptService
from apps.domains.results.services.manual_subjective_score import is_consumed_manual_total_placeholder
from apps.domains.results.services.question_stats_service import _current_question_facts
from apps.domains.results.utils.initial_exam_score import load_initial_exam_scores
from apps.support.results.tests.omr_subjective_completion_fixtures import (
    SubmissionViewSet, apply_omr_ai_result, dispatch_progress_pipeline, make_omr_review_fixture,
)
from apps.support.student_app.exam_dependencies import (
    student_exam_queryset, submission_status_map_for_student_exams,
)


@pytest.fixture
def scored_paper(db, settings):
    settings.OMR_SCORED_REASSIGNMENT_ENABLED = True
    tenant, exam, first, submission = make_omr_review_fixture(peer_phone="01099887766")
    second = apps.get_model("enrollment", "Enrollment").objects.get(
        tenant=tenant, lecture=first.lecture, student__phone="01099887766",
    )
    apply_omr_ai_result({
        "submission_id": submission.id, "tenant_id": tenant.id,
        "status": "DONE", "version": "v15", "aligned": True,
        "identifier": {"status": "ok", "identifier": first.student.omr_code},
        "answers": [
            {"question_id": 1, "detected": ["1"], "status": "ok", "confidence": .99},
            {"question_id": 2, "detected": ["2"], "status": "ok", "confidence": .99},
        ],
    })
    grade_submission(submission.id)
    submission.refresh_from_db()
    return tenant, exam, first, second, submission


def correct_student(tenant, submission, enrollment, **extra):
    request = APIRequestFactory().post("/submissions/manual-edit/", {
        "identifier": {"enrollment_id": enrollment.id}, "answers": [], **extra,
    }, format="json")
    request.tenant = tenant
    force_authenticate(request, user=submission.user)
    return SubmissionViewSet.as_view({"post": "manual_edit"})(request, pk=submission.id)


def test_scored_reassignment_moves_current_grade_and_keeps_original_facts(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    original = list(ResultFact.objects.filter(attempt__submission_id=submission.id).values())
    attempt_id = ExamAttempt.objects.get(submission_id=submission.id).id

    response = correct_student(tenant, submission, second)

    assert response.status_code == 200, response.data
    result = Result.objects.select_related("attempt").get(
        target_type="exam", target_id=exam.id, enrollment=second,
    )
    assert result.total_score == 100
    assert result.attempt_id == attempt_id
    assert result.attempt.enrollment_id == second.id
    assert not Result.objects.filter(target_type="exam", target_id=exam.id, enrollment=first).exists()
    assert not ExamAttempt.objects.filter(exam=exam, enrollment=first).exists()
    assert set(_current_question_facts(exam_id=exam.id).values_list("enrollment_id", flat=True)) == {second.id}
    assert list(ResultFact.objects.filter(id__in=[row["id"] for row in original]).values()) == original


def test_reassignment_round_trip_and_repeat_do_not_resurrect_old_facts(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    for target in (second, first, first):
        response = correct_student(tenant, submission, target)
        assert response.status_code == 200, response.data
        assert ExamAttempt.objects.filter(submission_id=submission.id).count() == 1
        result = Result.objects.get(target_type="exam", target_id=exam.id)
        assert result.enrollment_id == result.attempt.enrollment_id == target.id
        facts = _current_question_facts(exam_id=exam.id)
        assert facts.count() == 2
        assert set(facts.values_list("enrollment_id", flat=True)) == {target.id}
    assert ResultFact.objects.filter(source="omr_reassignment").count() == 2


def _state(submission):
    submission.refresh_from_db()
    return {
        "submission": type(submission).objects.filter(pk=submission.pk).values().get(),
        "answers": list(submission.answers.values()),
        "attempts": list(ExamAttempt.objects.order_by("id").values()),
        "results": list(Result.objects.order_by("id").values()),
        "items": list(ResultItem.objects.order_by("id").values()),
        "compatibility_results": list(ExamResult.objects.order_by("id").values()),
        "facts": list(ResultFact.objects.order_by("id").values()),
        "matches": list(apps.get_model("submissions", "OMRStudentMatch").objects.order_by("id").values()),
    }


def test_existing_teacher_grade_conflict_rolls_back_then_correct_target_succeeds(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    protected = ExamAttempt.objects.create(
        exam=exam, enrollment=second, submission_id=0, attempt_index=1,
        status="done", meta={"initial_snapshot": {"total_score": 73, "max_score": 100}},
    )
    Result.objects.create(
        target_type="exam", target_id=exam.id, enrollment=second,
        attempt=protected, total_score=73, objective_score=73, max_score=100,
    )
    before = _state(submission)
    response = correct_student(tenant, submission, second)
    assert response.status_code == 409
    assert response.data["code"] == "OMR_ATTEMPT_CONFLICT"
    assert _state(submission) == before
    response = correct_student(tenant, submission, first)
    assert response.status_code == 200, response.data
    assert Result.objects.get(enrollment=second).total_score == 73
    assert Result.objects.get(enrollment=first).total_score == 100


@pytest.mark.parametrize("failure_point", ["refresh_former_omr_student", "ResultItem.objects.bulk_create"])
def test_mid_correction_failure_rolls_back_every_mutation(scored_paper, monkeypatch, failure_point):
    from apps.domains.results.services import omr_reassignment, sync_result_from_submission

    tenant, exam, first, second, submission = scored_paper
    before = _state(submission)
    def fail(*args, **kwargs):
        raise RuntimeError("owned regression failure")
    if failure_point == "refresh_former_omr_student":
        monkeypatch.setattr(sync_result_from_submission, failure_point, fail)
    else:
        monkeypatch.setattr(omr_reassignment.ResultItem.objects, "bulk_create", fail)
    response = correct_student(tenant, submission, second)
    assert response.status_code == 500
    assert _state(submission) == before


@pytest.mark.parametrize("send_full_answers", [False, True])
def test_identity_only_correction_carries_teacher_total_items_and_original_time(scored_paper, send_full_answers):
    tenant, exam, first, second, submission = scored_paper
    result = Result.objects.get(enrollment=first)
    result.total_score = result.objective_score = 70
    result.save()
    item = result.items.order_by("question_id").first()
    item.score = 20
    item.source = "manual_grid"
    item.include_in_wrong_note = True
    item.save()
    fact = ResultFact.objects.create(
        target_type="exam", target_id=exam.id, enrollment=first, submission_id=submission.id,
        attempt=result.attempt, question_id=item.question_id, answer=item.answer,
        score=20, max_score=50, source="manual_grid", meta={"actor_user_id": submission.user_id},
    )
    before_items = list(result.items.order_by("question_id").values("question_id", "score", "source", "include_in_wrong_note"))
    submitted_at = result.submitted_at
    previous_target = None
    payload_answers = [
        {"question_id": row.exam_question_id, "answer": row.answer}
        for row in submission.answers.all()
    ] if send_full_answers else []
    for target in (second, second, first, first):
        before_facts = list(ResultFact.objects.current().order_by("id").values())
        before_item_rows = list(ResultItem.objects.order_by("id").values())
        response = correct_student(tenant, submission, target, answers=payload_answers)
        assert response.status_code == 200, response.data
        current = Result.objects.get(enrollment=target)
        assert current.total_score == current.objective_score == 70
        assert current.submitted_at == submitted_at
        assert list(current.items.order_by("question_id").values("question_id", "score", "source", "include_in_wrong_note")) == before_items
        current_fact = ResultFact.objects.current().get(source="manual_grid")
        assert current_fact.meta["attribution_origin"]["original_created_at"] == fact.created_at.isoformat()
        if previous_target == target.id:
            assert list(ResultFact.objects.current().order_by("id").values()) == before_facts
            assert list(ResultItem.objects.order_by("id").values()) == before_item_rows
        previous_target = target.id
    fact.refresh_from_db()
    assert fact.enrollment_id == first.id
    assert fact.meta == {"actor_user_id": submission.user_id}


def test_reassignment_with_a_real_answer_change_regrades_and_persists(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    answer = submission.answers.order_by("exam_question_id").first()
    response = correct_student(tenant, submission, second, answers=[{
        "question_id": answer.exam_question_id, "answer": "5",
    }])
    assert response.status_code == 200, response.data
    assert Result.objects.get(enrollment=second).total_score == 50
    answer.refresh_from_db()
    assert answer.answer == "5"
    assert correct_student(tenant, submission, second).status_code == 200
    assert Result.objects.get(enrollment=second).total_score == 50


def test_late_recognition_after_scored_identity_correction_keeps_grade(scored_paper):
    from academy.application.use_cases.omr.grading_readiness import grade_omr_submission_if_ready

    tenant, exam, first, second, submission = scored_paper
    assert correct_student(tenant, submission, second).status_code == 200
    before = _state(submission)
    apply_omr_ai_result({
        "submission_id": submission.id, "tenant_id": tenant.id,
        "status": "DONE", "version": "v15", "aligned": True,
        "identifier": {"status": "ok", "identifier": first.student.omr_code},
        "answers": [{"question_id": 1, "detected": ["5"], "status": "ok", "confidence": .99}],
    })
    decision = grade_omr_submission_if_ready(submission.id)
    assert not decision.graded
    assert decision.reason == "already_done"
    after = _state(submission)
    for key in ("answers", "attempts", "results", "items", "facts", "compatibility_results", "matches"):
        assert after[key] == before[key]


def test_consumed_manual_placeholder_does_not_revive_after_round_trip(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    attempt = ExamAttempt.objects.get(submission_id=submission.id)
    fact = ResultFact.objects.create(
        target_type="exam", target_id=exam.id, enrollment=first, submission_id=0,
        attempt=attempt, question_id=0, source="manual_total", score=0, max_score=100,
    )
    ResultFact.objects.filter(pk=fact.pk).update(created_at=timezone.now() - timedelta(hours=1))
    fact.refresh_from_db()
    attempt.meta["manual_score_placeholder"] = {"attached_at": (timezone.now() - timedelta(minutes=30)).isoformat()}
    attempt.save(update_fields=["meta"])
    assert is_consumed_manual_total_placeholder(fact=fact, attempt=attempt)
    for target in (second, first):
        response = correct_student(tenant, submission, target)
        assert response.status_code == 200, response.data
        attempt.refresh_from_db()
        copied = ResultFact.objects.current().get(source="manual_total")
        assert is_consumed_manual_total_placeholder(fact=copied, attempt=attempt)
    assert correct_student(tenant, submission, first).status_code == 200
    assert Result.objects.get(enrollment=first).total_score == 100


def _clinic(exam, enrollment, index):
    return ExamAttempt.objects.create(
        exam=exam, enrollment=enrollment, submission_id=None, attempt_index=index,
        is_retake=True, is_representative=False, status="done",
        meta={"source": "clinic_remediation", "total_score": 99, "max_score": 100},
    )


def test_clinic_history_never_becomes_first_score_and_does_not_consume_quota(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    clinic_a = _clinic(exam, first, 2)
    clinic_b = _clinic(exam, second, 1)
    before = list(ExamAttempt.objects.filter(pk__in=[clinic_a.id, clinic_b.id]).order_by("id").values())
    response = correct_student(tenant, submission, second)
    assert response.status_code == 200, response.data
    states = load_initial_exam_scores(exam_ids=[exam.id], enrollment_ids=[first.id, second.id])
    assert (exam.id, first.id) not in states
    assert states[(exam.id, second.id)].total_score == 100
    assert list(ExamAttempt.objects.filter(pk__in=[clinic_a.id, clinic_b.id]).order_by("id").values()) == before
    attempt = ExamAttemptService.create_for_submission(
        exam_id=exam.id, enrollment_id=first.id, submission_id=submission.id + 10000,
    )
    assert attempt.is_initial_grade
    assert not attempt.is_retake


def test_previous_teacher_representative_stays_and_becomes_first_without_changing_ordinal(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    old = ExamAttempt.objects.get(submission_id=submission.id)
    old.is_representative = False
    old.save(update_fields=["is_representative"])
    teacher = ExamAttempt.objects.create(
        exam=exam, enrollment=first, submission_id=0, attempt_index=3,
        is_retake=True, is_representative=True, status="done",
        meta={"total_score": 65, "max_score": 100, "source": "manual_entry"},
    )
    result = Result.objects.get(enrollment=first)
    result.attempt = teacher
    result.total_score = result.objective_score = 65
    result.save()
    before = list(Result.objects.filter(pk=result.id).values())
    response = correct_student(tenant, submission, second)
    assert response.status_code == 200, response.data
    assert list(Result.objects.filter(pk=result.id).values()) == before
    teacher.refresh_from_db()
    assert teacher.attempt_index == 3
    assert teacher.meta["initial_snapshot"]["total_score"] == 65
    assert load_initial_exam_scores(exam_ids=[exam.id], enrollment_ids=[first.id])[(exam.id, first.id)].total_score == 65


def test_disabled_activation_changes_no_data(scored_paper, settings):
    tenant, exam, first, second, submission = scored_paper
    settings.OMR_SCORED_REASSIGNMENT_ENABLED = False
    before = _state(submission)
    response = correct_student(tenant, submission, second)
    assert response.status_code == 409
    assert _state(submission) == before


def test_restores_previous_grade_after_moving_current_paper(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    paper = ExamAttempt.objects.get(submission_id=submission.id)
    paper.attempt_index = 2
    paper.is_retake = True
    paper.save(update_fields=["attempt_index", "is_retake"])
    previous = ExamAttempt.objects.create(
        exam=exam, enrollment=first, submission_id=0, attempt_index=1,
        is_representative=False, status="done",
        meta={"total_score": 60, "objective_score": 60, "max_score": 100,
              "initial_snapshot": {"total_score": 60, "max_score": 100}},
    )
    response = correct_student(tenant, submission, second)
    assert response.status_code == 200, response.data
    restored = Result.objects.get(enrollment=first)
    assert restored.attempt_id == previous.id
    assert restored.total_score == 60
    assert Result.objects.get(enrollment=second).total_score == 100


def test_progress_and_student_attempt_counts_follow_current_student(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    apps.get_model("progress", "ProgressPolicy").objects.update_or_create(
        lecture=first.lecture, defaults={"exam_start_session_order": 1},
    )
    _clinic(exam, first, 2)
    _clinic(exam, second, 1)
    dispatch_progress_pipeline(submission_id=submission.id)
    progress = apps.get_model("progress", "SessionProgress")
    assert progress.objects.get(enrollment=first).exam_attempted
    response = correct_student(tenant, submission, second)
    assert response.status_code == 200, response.data
    assert not progress.objects.get(enrollment=first).exam_attempted
    assert progress.objects.get(enrollment=second).exam_attempted
    for enrollment, count in ((first, 0), (second, 1)):
        exams = list(student_exam_queryset(enrollment.student, tenant))
        row = next(row for row in exams if row.id == exam.id)
        assert row.recorded_result_attempt_count == count
        status = submission_status_map_for_student_exams(
            tenant=tenant, student=enrollment.student, exams=exams,
        )
        assert status.get(exam.id, {}).get("attempt_count", 0) == count


def test_other_tenant_student_is_rejected_without_any_change(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    other_tenant = apps.get_model("core", "Tenant").objects.create(code="omr_correction_other", name="Other")
    from django.contrib.auth import get_user_model
    foreign_user = get_user_model().objects.create_user(username="foreign-omr-student", tenant=other_tenant)
    student = apps.get_model("students", "Student").objects.create(
        tenant=other_tenant, user=foreign_user, name="Foreign student", ps_number="foreign-omr",
    )
    lecture = apps.get_model("lectures", "Lecture").objects.create(
        tenant=other_tenant, name="Foreign", title="Foreign", subject="MATH",
    )
    foreign = apps.get_model("enrollment", "Enrollment").objects.create(
        tenant=other_tenant, student=student, lecture=lecture, status="ACTIVE",
    )
    before = _state(submission)
    response = correct_student(tenant, submission, foreign)
    assert response.status_code == 400
    assert _state(submission) == before


def test_legacy_target_first_score_is_preserved_when_retake_is_allowed(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    exam.allow_retake = True
    exam.max_attempts = 2
    exam.save(update_fields=["allow_retake", "max_attempts"])
    old = ExamAttempt.objects.create(
        exam=exam, enrollment=second, submission_id=0, attempt_index=1,
        status="done", meta={},
    )
    Result.objects.create(
        target_type="exam", target_id=exam.id, enrollment=second, attempt=old,
        total_score=73, objective_score=73, max_score=100,
    )
    response = correct_student(tenant, submission, second)
    assert response.status_code == 200, response.data
    assert Result.objects.get(enrollment=second).total_score == 100
    state = load_initial_exam_scores(exam_ids=[exam.id], enrollment_ids=[second.id])
    assert state[(exam.id, second.id)].total_score == 73
    old.refresh_from_db()
    assert old.meta["final_result_snapshot"]["total_score"] == 73


def test_preexisting_corrupt_target_result_is_preserved_for_explicit_repair(scored_paper):
    tenant, exam, first, second, submission = scored_paper
    paper = ExamAttempt.objects.get(submission_id=submission.id)
    Result.objects.create(
        target_type="exam", target_id=exam.id, enrollment=second, attempt=paper,
        total_score=73, objective_score=73, max_score=100,
    )
    before = _state(submission)
    response = correct_student(tenant, submission, second)
    assert response.status_code == 409
    assert response.data["code"] == "OMR_ATTEMPT_CONFLICT"
    assert _state(submission) == before


def test_internal_fact_cutoff_does_not_expand_the_public_attempt_contract(scored_paper):
    from apps.domains.results.serializers.exam_attempt import ExamAttemptSerializer

    _, _, _, _, submission = scored_paper
    attempt = ExamAttempt.objects.get(submission_id=submission.id)
    assert "current_fact_after_id" not in ExamAttemptSerializer(attempt).data
