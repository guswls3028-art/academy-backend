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


def get_matchup_document_models():
    from apps.domains.matchup.models import MatchupDocument, MatchupProblem

    return MatchupDocument, MatchupProblem


def get_matchup_page_state_model():
    from apps.domains.matchup.models import MatchupPageState

    return MatchupPageState


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


def get_matchup_proposal_model():
    from apps.domains.matchup.models import ProblemSegmentationProposal

    return ProblemSegmentationProposal


def approve_matchup_proposal(*args, **kwargs):
    from apps.domains.matchup.proposal_helpers import approve_proposal

    return approve_proposal(*args, **kwargs)


def get_matchup_proposal_approval_error():
    from apps.domains.matchup.proposal_helpers import ProposalApprovalError

    return ProposalApprovalError
