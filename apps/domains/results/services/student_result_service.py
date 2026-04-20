# PATH: apps/domains/results/services/student_result_service.py
"""
단일 진실: 학생용 시험 결과 조회
- GET /results/me/exams/<exam_id>/ (results 앱)
- GET /student/results/me/exams/<exam_id>/ (student_app)
둘 다 이 서비스의 동일 데이터를 반환하도록 사용.
"""
from __future__ import annotations

from django.http import Http404

from apps.domains.results.models import Result, ExamAttempt, ExamResult
from apps.domains.results.serializers.student_exam_result import StudentExamResultSerializer
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.enrollment.models import Enrollment
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.clinic import is_clinic_required
from apps.domains.results.utils.ranking import compute_exam_rankings
from apps.domains.progress.models import ClinicLink


def get_my_exam_result_data(request, exam_id: int, tenant=None) -> dict:
    """
    현재 사용자의 시험 결과 스냅샷 + 재시험/클리닉 정책.
    enrollment/result 없으면 Http404.
    tenant: 테넌트 격리를 위해 반드시 전달해야 함.
    """
    user = request.user
    if tenant is None:
        tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("tenant resolution failed")
    exam_id = int(exam_id)
    exam = Exam.objects.filter(id=exam_id).first()
    if not exam:
        raise Http404("exam not found")

    # 해당 시험의 응시 대상 enrollment만 사용 (한 학생이 여러 강의 수강 시 정확한 결과 조회)
    allowed_enrollment_ids = ExamEnrollment.objects.filter(
        exam_id=exam_id
    ).values_list("enrollment_id", flat=True)
    # ⚠️ tenant 필터 필수: 타 테넌트 enrollment 접근 차단
    enrollment_qs = Enrollment.objects.filter(id__in=allowed_enrollment_ids, tenant=tenant)
    enrollment_qs = enrollment_qs.filter(student__user=user)

    enrollment = enrollment_qs.first()
    if not enrollment:
        raise Http404("enrollment not found")

    enrollment_id = int(enrollment.id)

    result = (
        Result.objects
        .filter(target_type="exam", target_id=exam_id, enrollment_id=enrollment_id)
        .prefetch_related("items")
        .order_by("-id")
        .first()
    )
    if not result:
        raise Http404("result not found")

    allow_retake = bool(getattr(exam, "allow_retake", False))
    max_attempts = int(getattr(exam, "max_attempts", 1) or 1)
    attempt_count = ExamAttempt.objects.filter(
        exam_id=exam_id,
        enrollment_id=enrollment_id,
    ).count()
    can_retake = bool(allow_retake and attempt_count < max_attempts)

    clinic_required = False
    session = get_primary_session_for_exam(exam_id)
    if session:
        clinic_required = is_clinic_required(
            session=session,
            enrollment_id=enrollment_id,
            include_manual=False,
        )

    data = StudentExamResultSerializer(result).data
    data["allow_retake"] = allow_retake
    data["max_attempts"] = max_attempts
    data["can_retake"] = can_retake
    data["clinic_required"] = bool(clinic_required)

    # ✅ 미응시 감지: ExamAttempt.meta.status
    _attempt_meta_status = None
    if result.attempt_id:
        _att = ExamAttempt.objects.filter(id=int(result.attempt_id)).only("meta").first()
        if _att:
            _attempt_meta_status = (_att.meta or {}).get("status")
    is_not_submitted = (_attempt_meta_status == "NOT_SUBMITTED")
    data["meta_status"] = _attempt_meta_status

    data["exam_id"] = exam_id
    pass_score = float(getattr(exam, "pass_score", 0) or 0)
    # 미응시 → is_pass=None / pass_score=0(기준 미설정) → is_pass=None
    if is_not_submitted:
        data["is_pass"] = None
    elif pass_score > 0:
        data["is_pass"] = float(result.total_score) >= pass_score
    else:
        data["is_pass"] = None

    # ✅ 드리프트 해소: 클리닉 재시험 통과 상태 반영
    # Result.total_score는 1차 결과 고정이지만, 클리닉 재시험(ClinicLink.EXAM_PASS)
    # 으로 최종 합격한 경우 학생에게 명시. 최종 합격 판정은 final_pass로 통합.
    remediated = False
    clinic_retake_info = None
    if session:
        clinic_pass_link = (
            ClinicLink.objects
            .filter(
                enrollment_id=enrollment_id,
                session=session,
                source_type="exam",
                source_id=exam_id,
                resolved_at__isnull=False,
                resolution_type=ClinicLink.ResolutionType.EXAM_PASS,
            )
            .order_by("-resolved_at")
            .first()
        )
        if clinic_pass_link:
            remediated = True
            evidence = clinic_pass_link.resolution_evidence or {}
            clinic_retake_info = {
                "score": evidence.get("score"),
                "pass_score": evidence.get("pass_score"),
                "attempt_id": evidence.get("attempt_id"),
                "resolved_at": clinic_pass_link.resolved_at.isoformat()
                    if clinic_pass_link.resolved_at else None,
            }

    data["remediated"] = remediated
    data["clinic_retake"] = clinic_retake_info
    # final_pass: 1차 합격 OR 클리닉 재시험 통과
    base_pass = data["is_pass"]
    if is_not_submitted and not remediated:
        data["final_pass"] = None
    elif base_pass is True or remediated:
        data["final_pass"] = True
    elif base_pass is False:
        data["final_pass"] = False
    else:
        data["final_pass"] = None

    # ✅ DRAFT 중간 점수 노출 방어
    # Result가 채점 중 상태면 provisional로 표기. submission→ExamResult→status 체인 확인.
    is_provisional = False
    if result.attempt_id:
        att = ExamAttempt.objects.filter(id=int(result.attempt_id)).only("submission_id").first()
        if att and att.submission_id:
            er_status = (
                ExamResult.objects.filter(submission_id=att.submission_id)
                .values_list("status", flat=True)
                .first()
            )
            if er_status and er_status != ExamResult.Status.FINAL:
                is_provisional = True
    data["is_provisional"] = is_provisional

    # 정답 공개 정책 적용
    # provisional/미응시/불합격 → 비공개, 합격/기준없음 → 정책 따름
    is_pass = data["is_pass"]
    if is_provisional or is_not_submitted:
        show_answers = False
    elif is_pass is None or is_pass:
        show_answers = exam.should_show_answers()
    else:
        show_answers = False
    data["answer_visibility"] = getattr(exam, "answer_visibility", "hidden")
    data["answers_visible"] = show_answers

    # question_id → question_number 매핑 (ExamQuestion.number 사용)
    from apps.domains.exams.models.question import ExamQuestion
    item_question_ids = [
        item.get("question_id") for item in (data.get("items") or [])
        if item.get("question_id")
    ]
    question_number_map = {}
    if item_question_ids:
        question_number_map = dict(
            ExamQuestion.objects.filter(id__in=item_question_ids)
            .values_list("id", "number")
        )

    # 정답 공개 시 answer key에서 correct_answer 주입
    correct_answer_map = {}
    if show_answers:
        from apps.domains.exams.models import AnswerKey
        template_exam_id = exam.effective_template_exam_id
        ak = AnswerKey.objects.filter(exam_id=template_exam_id).first()
        if ak and ak.answers:
            correct_answer_map = ak.answers  # key=question_id(str), value=answer

    for item in data.get("items") or []:
        q_id = item.get("question_id")
        item["question_number"] = question_number_map.get(q_id, q_id)
        item.setdefault("student_answer", item.get("answer"))
        if show_answers:
            item["correct_answer"] = correct_answer_map.get(str(q_id or "")) or None
        else:
            item["correct_answer"] = None

    # 석차 정보 추가
    rank_map = compute_exam_rankings(exam_id=exam_id)
    rank_info = rank_map.get(enrollment_id, {})
    data["rank"] = rank_info.get("rank")
    data["percentile"] = rank_info.get("percentile")
    data["cohort_size"] = rank_info.get("cohort_size")
    data["cohort_avg"] = rank_info.get("cohort_avg")

    return data
