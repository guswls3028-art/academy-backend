"""Cross-domain dependency loaders for AI callbacks."""

from __future__ import annotations


def get_submission_ai_result_applier():
    from apps.domains.submissions.services.ai_omr_result_mapper import apply_ai_result

    return apply_ai_result


def get_exam_segmentation_models():
    from apps.domains.exams.models import (
        Exam,
        ExamQuestion,
        QuestionExplanation,
        Sheet,
    )

    return Exam, Sheet, ExamQuestion, QuestionExplanation


def get_exam_question_proposal_model():
    from apps.domains.exams.models import ExamQuestionProposal

    return ExamQuestionProposal


def get_matchup_document_models():
    from apps.domains.matchup.models import MatchupDocument, MatchupProblem

    return MatchupDocument, MatchupProblem


def get_matchup_page_state_model():
    from apps.domains.matchup.models import MatchupPageState

    return MatchupPageState


def protected_matchup_problem_ids(problem_queryset):
    from apps.domains.matchup.services import protected_matchup_problem_ids as get_ids

    return get_ids(problem_queryset)


def schedule_detached_matchup_auto_images(*, tenant_id, image_keys):
    from apps.domains.matchup.analysis_artifacts import schedule_detached_auto_images

    return schedule_detached_auto_images(tenant_id=tenant_id, image_keys=image_keys)


def detached_matchup_auto_image_keys(rows):
    from apps.domains.matchup.analysis_artifacts import detached_auto_image_keys

    return detached_auto_image_keys(rows)


def schedule_unreferenced_matchup_artifacts(*, tenant_id, document_id, job_id):
    from apps.domains.matchup.analysis_artifacts import schedule_unreferenced_analysis_artifacts

    return schedule_unreferenced_analysis_artifacts(
        tenant_id=tenant_id, document_id=document_id, job_id=job_id,
    )


def handle_matchup_proposal_path(**kwargs):
    from apps.domains.matchup.services_proposal import (
        handle_matchup_proposal_path as handle,
    )

    return handle(**kwargs)


def get_auto_segmentation_snapshot_model():
    from apps.domains.matchup.models import AutoSegmentationSnapshot

    return AutoSegmentationSnapshot


def invalidate_matchup_tenant_similar_cache(tenant_id):
    from apps.domains.matchup.cache import invalidate_tenant_similar_cache

    invalidate_tenant_similar_cache(tenant_id)


def get_post_entity_model():
    from apps.domains.community.models import PostEntity

    return PostEntity


def get_matchup_problem_model():
    from apps.domains.matchup.models import MatchupProblem

    return MatchupProblem


def get_wrong_note_pdf_callback_dependencies():
    from apps.domains.results.models import WrongNotePDF
    from apps.domains.results.services.wrong_note_pdf_service import (
        wrong_note_pdf_storage_key,
    )

    return WrongNotePDF, wrong_note_pdf_storage_key
