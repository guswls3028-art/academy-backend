# PATH: apps/domains/results/views/session_scores_view.py
"""
SessionScores API (FOR FRONTEND SCORE TAB)

GET /api/v1/results/admin/sessions/<session_id>/scores/

✅ 목적
- 성적 탭 메인 테이블에서 학생별 시험/과제 요약 + 편집 상태 표시
- results + homework_results + progress 데이터를 "조합"만 한다.

✅ 삭제된 학생(유령 학생) 제외
- student.deleted_at IS NOT NULL 인 학생은 수강/성적 목록에서 제외 (Enrollment 필터).
- 수강·출결·클리닉 파이프라인과 동일 정합성 유지.

🚫 금지
- 점수 계산/정책 생성
- homework percent / cutline 계산
- progress 세부 계산 결과 직접 노출(progress_status 최종 판정값만 계약)

✅ 단일 진실
- exam: results(Result + Exam.pass_score)
- homework: homework_results.HomeworkScore
- progress_status: progress.SessionProgress.completed
- clinic_required: progress.ClinicLink(is_auto=True) 중 progress_status=completed가 아닌 현재 대상

📌 중요 설계 결정
- enrollment 모수는 SessionProgress ❌
- 성적탭 row 모수는 차시 출석/수강 roster ✅
- OMR 시험 대상은 ExamEnrollment + 시험이 붙은 차시의 SessionEnrollment ✅
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.domains.results.utils.result_queries import latest_results_per_enrollment
from apps.domains.results.utils.exam_achievement import compute_exam_achievement_bulk
from apps.support.omr.score_shape import get_exam_score_shape
from apps.domains.results.serializers.session_scores import SessionScoreRowSerializer

from apps.domains.lectures.models import Session
from apps.domains.progress.models import ClinicLink, SessionProgress
from apps.domains.clinic.models import SessionParticipant

from apps.domains.homework_results.models import HomeworkScore
from apps.domains.homework_results.models import Homework
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.attendance.models import Attendance
from apps.domains.submissions.models import Submission

from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import ExamEnrollment, ExamQuestion
from apps.domains.exams.models.sheet import Sheet
from apps.domains.exams.services.template_resolver import resolve_template_exam


def _get_profile_photo_url(student) -> Optional[str]:
    """학생 프로필 사진 R2 presigned URL 반환 (없으면 None)."""
    r2_key = getattr(student, "profile_photo_r2_key", None) or ""
    if r2_key:
        try:
            from django.conf import settings
            from libs.r2_client.presign import create_presigned_get_url
            return create_presigned_get_url(r2_key, expires_in=3600, bucket=settings.R2_STORAGE_BUCKET)
        except Exception:
            pass
    return None


def _get_enrollment_display_fields(enrollment: Optional[Enrollment]) -> dict:
    """Enrollment에서 학생 SSOT 표시 필드 추출 (아바타 + 강의 딱지)."""
    if not enrollment:
        return {"profile_photo_url": None, "lecture_title": None, "lecture_color": None, "lecture_chip_label": None}
    student = getattr(enrollment, "student", None)
    lecture = getattr(enrollment, "lecture", None)
    return {
        "profile_photo_url": _get_profile_photo_url(student) if student else None,
        "lecture_title": getattr(lecture, "title", None) if lecture else None,
        "lecture_color": getattr(lecture, "color", None) if lecture else None,
        "lecture_chip_label": getattr(lecture, "chip_label", None) if lecture else None,
    }


def _safe_student_name(enrollment: Optional[Enrollment]) -> str:
    if not enrollment:
        return "-"

    try:
        if hasattr(enrollment, "student") and enrollment.student:
            for k in ("name", "full_name", "username"):
                v = getattr(enrollment.student, k, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()

        if hasattr(enrollment, "user") and enrollment.user:
            for k in ("name", "full_name", "username", "first_name"):
                v = getattr(enrollment.user, k, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()

        for k in ("student_name", "name", "title"):
            v = getattr(enrollment, k, None)
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception:
        pass

    return "-"


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clinic_source_id(row: Dict[str, Any], source_type: str) -> Optional[int]:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    if row.get("source_type") == source_type:
        return _int_or_none(row.get("source_id"))
    if row.get("source_type") is None:
        return _int_or_none(meta.get(f"{source_type}_id"))
    return None


def _is_live_session_clinic_link(
    row: Dict[str, Any],
    *,
    live_exam_ids: Set[int],
    live_homework_ids: Set[int],
    homework_assigned_set: Set[tuple[int, int]],
) -> bool:
    enrollment_id = _int_or_none(row.get("enrollment_id"))
    if enrollment_id is None:
        return False

    exam_id = _clinic_source_id(row, "exam")
    if exam_id is not None:
        return exam_id in live_exam_ids

    homework_id = _clinic_source_id(row, "homework")
    if homework_id is not None:
        return (
            homework_id in live_homework_ids
            and (enrollment_id, homework_id) in homework_assigned_set
        )

    # Legacy automatic links without source metadata are already session-scoped.
    # Keep them visible rather than silently hiding an ambiguous historical target.
    return row.get("source_type") is None


def _build_exam_attempt_summary(
    *,
    attempt: ExamAttempt,
    result: Optional[Result],
    exam_pass_score: float,
    exam_max_score: float,
) -> Dict[str, Any]:
    meta = attempt.meta if isinstance(attempt.meta, dict) else {}
    snapshot = meta.get("initial_snapshot") if isinstance(meta.get("initial_snapshot"), dict) else {}
    meta_status = meta.get("status")
    is_not_submitted = meta_status == "NOT_SUBMITTED"

    score: Optional[float]
    if is_not_submitted:
        score = None
    elif int(attempt.attempt_index) == 1:
        score = (
            _float_or_none(snapshot.get("total_score"))
            if snapshot
            else None
        )
        if score is None:
            score = _float_or_none(meta.get("total_score"))
        if score is None and result is not None:
            score = _float_or_none(result.total_score)
    else:
        score = _float_or_none(meta.get("total_score"))

    max_score = _float_or_none(meta.get("max_score"))
    if int(attempt.attempt_index) == 1:
        max_score = _float_or_none(snapshot.get("max_score")) if snapshot else max_score
    if max_score is None and result is not None:
        max_score = _float_or_none(result.max_score)
    if max_score is None:
        max_score = float(exam_max_score)

    pass_score = _float_or_none(meta.get("pass_score"))
    if pass_score is None:
        pass_score = float(exam_pass_score)

    passed = None
    if score is not None and pass_score is not None and pass_score > 0:
        passed = bool(float(score) >= float(pass_score))

    entry: Dict[str, Any] = {
        "attempt_index": int(attempt.attempt_index),
        "score": score,
        "max_score": max_score,
        "pass_score": pass_score,
        "passed": passed,
        "at": attempt.created_at,
        "source": "clinic" if attempt.clinic_link_id else "grade",
    }
    if meta_status:
        entry["meta_status"] = meta_status
    return entry


class SessionScoresView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, session_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "Tenant required"},
                status=403,
            )
        session = get_object_or_404(
            Session,
            id=int(session_id),
            lecture__tenant=tenant,
        )

        # -------------------------------------------------
        # 0) Exams
        # -------------------------------------------------
        exams = list(get_exams_for_session(session).filter(tenant=tenant))
        exam_ids = [int(e.id) for e in exams]

        # -------------------------------------------------
        # 1) Enrollment 모수
        # -------------------------------------------------
        active_session_enrollment_ids = list(
            SessionEnrollment.objects
            .filter(
                tenant=tenant,
                session=session,
                enrollment__status="ACTIVE",
                enrollment__student__deleted_at__isnull=True,
            )
            .values_list("enrollment_id", flat=True)
            .distinct()
        )
        attendance_enrollment_ids = list(
            Attendance.objects
            .filter(
                tenant=tenant,
                session=session,
                enrollment__status="ACTIVE",
                enrollment__student__deleted_at__isnull=True,
            )
            .values_list("enrollment_id", flat=True)
            .distinct()
        )
        if attendance_enrollment_ids:
            active_session_enrollment_ids = attendance_enrollment_ids

        # SSOT: 성적탭은 차시에 붙은 학생 roster를 먼저 보여준다.
        # OMR 채점은 명시 ExamEnrollment가 없어도 이 roster 학생을 후보로 삼고,
        # 채점/수동입력 시점에 ExamEnrollment를 materialize한다.
        enrollment_qs = (
            Enrollment.objects.filter(
                id__in=active_session_enrollment_ids,
                tenant=tenant,
            )
            .filter(status="ACTIVE")
            .filter(student__deleted_at__isnull=True)
            .distinct()
        )

        enrollment_ids = list(enrollment_qs.values_list("id", flat=True))

        # -------------------------------------------------
        # 1b) 학생별 시험/과제 등록 여부 맵 (미등록 컬럼 비활성화용)
        # -------------------------------------------------
        exam_enrolled_set: Set[tuple[int, int]] = set()
        if exam_ids:
            for row in ExamEnrollment.objects.filter(
                exam_id__in=exam_ids
            ).values_list("enrollment_id", "exam_id"):
                exam_enrolled_set.add((int(row[0]), int(row[1])))
            for eid in active_session_enrollment_ids:
                for exid in exam_ids:
                    exam_enrolled_set.add((int(eid), int(exid)))

        hw_assigned_set: Set[tuple[int, int]] = set()
        for row in HomeworkAssignment.objects.filter(
            session=session
        ).values_list("enrollment_id", "homework_id"):
            hw_assigned_set.add((int(row[0]), int(row[1])))

        # -------------------------------------------------
        # 2) Meta (프론트 계약)
        # -------------------------------------------------
        homeworks = list(
            Homework.objects
            .filter(session=session)
            .exclude(meta__removed_from_session_at__isnull=False)
            .order_by("display_order", "created_at", "id")
        )

        # 시험별 문항(주관식 점수 입력용): template → Sheet → ExamQuestion
        template_by_exam: Dict[int, Any] = {}
        for ex in exams:
            try:
                template_by_exam[int(ex.id)] = resolve_template_exam(ex)
            except Exception:
                template_by_exam[int(ex.id)] = ex
        template_ids = list({int(t.id) for t in template_by_exam.values()})
        sheets = list(Sheet.objects.filter(exam_id__in=template_ids))
        sheet_by_template = {int(s.exam_id): s for s in sheets}
        all_questions = list(
            ExamQuestion.objects.filter(
                sheet_id__in=[s.id for s in sheets]
            ).order_by("sheet_id", "number")
        )
        questions_by_sheet: Dict[int, List[Dict[str, Any]]] = {}
        for q in all_questions:
            questions_by_sheet.setdefault(q.sheet_id, []).append({
                "question_id": q.id,
                "number": q.number,
                "max_score": float(q.score or 0.0),
            })
        exam_questions_map: Dict[int, List[Dict[str, Any]]] = {}
        for ex in exams:
            template = template_by_exam.get(int(ex.id))
            sheet = sheet_by_template.get(int(template.id)) if template else None
            if not sheet:
                exam_questions_map[int(ex.id)] = []
            else:
                exam_questions_map[int(ex.id)] = questions_by_sheet.get(sheet.id, [])

        score_shape_by_exam = {int(ex.id): get_exam_score_shape(ex) for ex in exams}
        for exid, questions in exam_questions_map.items():
            score_shape = score_shape_by_exam.get(int(exid))
            if score_shape is None:
                continue
            for question in questions:
                kind = score_shape.question_kind_by_id.get(int(question["question_id"]))
                if kind:
                    question["kind"] = kind
                question["max_score"] = score_shape.question_max_score(
                    int(question["question_id"]),
                    question.get("max_score"),
                )

        # 시험/과제를 display_order 기준으로 정렬 (0이면 created_at 순)
        exams = sorted(exams, key=lambda e: (getattr(e, "display_order", 0) or 0, e.created_at, e.id))
        exam_ids = [int(e.id) for e in exams]
        homeworks = sorted(homeworks, key=lambda h: (getattr(h, "display_order", 0) or 0, h.created_at, h.id))
        homework_ids = [int(hw.id) for hw in homeworks]

        # Homework 대표 max_score: HomeworkScore 레코드에서 집계 (과제별 최대값, 없으면 100)
        hw_max_scores: Dict[int, float] = {}
        if homeworks:
            from django.db.models import Max
            hw_max_agg = (
                HomeworkScore.objects
                .filter(homework_id__in=homework_ids)
                .values("homework_id")
                .annotate(rep_max=Max("max_score"))
            )
            for row in hw_max_agg:
                if row["rep_max"] is not None:
                    hw_max_scores[int(row["homework_id"])] = float(row["rep_max"])

        # SSOT (2026-05-13): 발송 컨텍스트 — frontend가 어디서든 정확히 강의명/차시명 인용 가능하도록 응답 meta에 항상 포함.
        # 직전 결함: drawer 발송 path가 row.lecture_title / qc.getQueryData fallback에 의존 → 캐시 miss 시 알림톡 봉투 변수 빈 값으로 발송 (학원장 limglish 보고).
        _session_lecture = getattr(session, "lecture", None)
        response_meta = {
            "session_title": str(getattr(session, "title", "") or ""),
            "lecture_title": str(getattr(_session_lecture, "title", "") or ""),
            "lecture_id": int(_session_lecture.id) if _session_lecture is not None else None,
            "exams": [
                {
                    "exam_id": int(ex.id),
                    "title": str(getattr(ex, "title", "")),
                    "pass_score": float(getattr(ex, "pass_score", 0.0) or 0.0),
                    "max_score": float(getattr(ex, "max_score", 100.0) or 100.0),
                    "choice_count": int(score_shape_by_exam[int(ex.id)].choice_count),
                    "essay_count": int(score_shape_by_exam[int(ex.id)].essay_count),
                    "objective_max_score": float(score_shape_by_exam[int(ex.id)].objective_max_score),
                    "subjective_max_score": float(score_shape_by_exam[int(ex.id)].subjective_max_score),
                    "score_shape_source": str(score_shape_by_exam[int(ex.id)].shape_source),
                    "display_order": int(getattr(ex, "display_order", 0) or 0),
                    "questions": exam_questions_map.get(int(ex.id), []),
                }
                for ex in exams
            ],
            "homeworks": [
                {
                    "homework_id": int(hw.id),
                    "title": str(hw.title),
                    "unit": None,  # 서버 단일 진실
                    "max_score": hw_max_scores.get(int(hw.id), 100.0),
                    "display_order": int(getattr(hw, "display_order", 0) or 0),
                }
                for hw in homeworks
            ],
        }

        if not enrollment_ids:
            return Response({"meta": response_meta, "rows": []})

        # -------------------------------------------------
        # 3) 진행/완료 + Clinic 대상자
        # -------------------------------------------------
        progress_completed_ids: Set[int] = set(
            SessionProgress.objects.filter(
                session=session,
                enrollment_id__in=enrollment_ids,
                completed=True,
            )
            .values_list("enrollment_id", flat=True)
            .distinct()
        )

        clinic_link_rows = list(
            ClinicLink.objects.filter(
                session=session,
                enrollment_id__in=enrollment_ids,
                is_auto=True,
                resolved_at__isnull=True,
            )
            .values("enrollment_id", "source_type", "source_id", "meta")
            .order_by("id")
        )
        live_exam_ids = set(exam_ids)
        live_homework_ids = set(homework_ids)
        raw_clinic_ids: Set[int] = {
            int(row["enrollment_id"])
            for row in clinic_link_rows
            if _is_live_session_clinic_link(
                row,
                live_exam_ids=live_exam_ids,
                live_homework_ids=live_homework_ids,
                homework_assigned_set=hw_assigned_set,
            )
        }
        # 최종 완료 상태가 SSOT다. 과거/특례 등록으로 남은 미해소 ClinicLink가 있어도
        # SessionProgress.completed=True면 현재 클리닉 대상에서 제외한다.
        clinic_ids: Set[int] = raw_clinic_ids - progress_completed_ids

        # -------------------------------------------------
        # 3b) 클리닉 수강 완료(enrollment별 ATTENDED 1건 이상) → 하이라이트 제거
        # ⚠️ tenant 필터 필수: enrollment_id 만으로 필터 시 타 테넌트 데이터 혼입 가능
        # -------------------------------------------------
        enrollment_ids_clinic_attended: Set[int] = set(
            SessionParticipant.objects.filter(
                tenant=tenant,
                enrollment_id__in=enrollment_ids,
                status=SessionParticipant.Status.ATTENDED,
            )
            .values_list("enrollment_id", flat=True)
            .distinct()
        )

        # -------------------------------------------------
        # 3c) 클리닉 예약 있음(enrollment별 PENDING/BOOKED 1건 이상)
        # -------------------------------------------------
        enrollment_ids_with_clinic_booking: Set[int] = set(
            SessionParticipant.objects.filter(
                tenant=tenant,
                enrollment_id__in=enrollment_ids,
                status__in=[
                    SessionParticipant.Status.PENDING,
                    SessionParticipant.Status.BOOKED,
                ],
            )
            .values_list("enrollment_id", flat=True)
            .distinct()
        )

        # -------------------------------------------------
        # 4) Enrollment → student_name
        # -------------------------------------------------
        # 🔐 tenant 강제 — enrollment_ids는 위에서 tenant 필터를 거쳤지만 명시적으로 한 번 더.
        enrollment_map = {
            int(e.id): e
            for e in Enrollment.objects.filter(id__in=enrollment_ids, tenant=tenant).select_related("student", "lecture")
        }

        student_name_map = {
            eid: _safe_student_name(enrollment_map.get(eid))
            for eid in enrollment_ids
        }
        student_id_map = {
            eid: getattr(enrollment_map.get(eid), "student_id", None)
            for eid in enrollment_ids
        }

        # -------------------------------------------------
        # 5) HomeworkScore map (enrollment → homework → score)
        # -------------------------------------------------
        # ✅ 성적 산출: attempt_index=1 (1차) 만 사용
        hw_scores = HomeworkScore.objects.filter(
            session=session,
            enrollment_id__in=enrollment_ids,
            attempt_index=1,
        )

        hw_map: Dict[int, Dict[int, HomeworkScore]] = {}
        for hs in hw_scores:
            hw_map.setdefault(int(hs.enrollment_id), {})[int(hs.homework_id)] = hs

        # -------------------------------------------------
        # 6) Exam Result map (문항별 점수용 items prefetch)
        # -------------------------------------------------
        result_map: Dict[int, Dict[int, Result]] = {}
        for exid in exam_ids:
            rs = (
                latest_results_per_enrollment(
                    target_type="exam",
                    target_id=int(exid),
                )
                .filter(enrollment_id__in=enrollment_ids)
                .prefetch_related("items")
            )
            result_map[int(exid)] = {int(r.enrollment_id): r for r in rs}

        omr_review_map: Dict[tuple[int, int], Dict[str, Any]] = {}
        if exam_ids and enrollment_ids:
            omr_submissions = (
                Submission.objects
                .filter(
                    tenant=tenant,
                    target_type=Submission.TargetType.EXAM,
                    target_id__in=exam_ids,
                    enrollment_id__in=enrollment_ids,
                    source=Submission.Source.OMR_SCAN,
                )
                .order_by("target_id", "enrollment_id", "-id")
            )
            for sub in omr_submissions:
                if not sub.enrollment_id:
                    continue
                key = (int(sub.target_id), int(sub.enrollment_id))
                if key in omr_review_map:
                    continue
                submission_meta = sub.meta if isinstance(sub.meta, dict) else {}
                manual_review = (
                    submission_meta.get("manual_review")
                    if isinstance(submission_meta.get("manual_review"), dict)
                    else {}
                )
                if manual_review.get("required") is True:
                    omr_review_map[key] = {
                        "status": "OMR_REVIEW_REQUIRED",
                        "manual_review_required": True,
                        "manual_review_reasons": list(manual_review.get("reasons") or []),
                        "submission_id": int(sub.id),
                    }

        # -------------------------------------------------
        # 7) Attempt LOCK 상태
        # -------------------------------------------------
        attempt_ids = {
            int(r.attempt_id)
            for per_exam in result_map.values()
            for r in per_exam.values()
            if r.attempt_id
        }

        _attempt_qs = list(ExamAttempt.objects.filter(id__in=attempt_ids))
        attempt_status_map = {
            int(a.id): str(a.status or "")
            for a in _attempt_qs
        }
        # ✅ 미응시(NOT_SUBMITTED) 상태 맵
        attempt_meta_status_map: Dict[int, Optional[str]] = {
            int(a.id): (a.meta or {}).get("status")
            for a in _attempt_qs
        }

        # -------------------------------------------------
        # 7-a) Exam 메타
        # -------------------------------------------------
        exam_pass_score_map = {
            int(ex.id): float(getattr(ex, "pass_score", 0.0) or 0.0)
            for ex in exams
        }
        exam_max_score_map = {
            int(ex.id): float(getattr(ex, "max_score", 100.0) or 100.0)
            for ex in exams
        }
        exam_title_map = {
            int(ex.id): str(getattr(ex, "title", "") or "")
            for ex in exams
        }

        # -------------------------------------------------
        # 7-b) Attempt count & clinic_link_id bulk (차수별 편집 지원)
        # -------------------------------------------------
        from django.db.models import Count

        # Exam: 차수(attempt) 수
        exam_attempt_stats = (
            ExamAttempt.objects
            .filter(exam_id__in=exam_ids, enrollment_id__in=enrollment_ids)
            .values("exam_id", "enrollment_id")
            .annotate(count=Count("id"))
        )
        exam_attempt_count_map: Dict[tuple, int] = {
            (int(row["exam_id"]), int(row["enrollment_id"])): row["count"]
            for row in exam_attempt_stats
        }

        # Exam: 미해소 ClinicLink ID (source_type=exam 기준)
        exam_clinic_link_qs = (
            ClinicLink.objects
            .filter(
                session=session,
                enrollment_id__in=clinic_ids,
                source_type="exam",
                source_id__in=exam_ids,
                resolved_at__isnull=True,
            )
            .values("enrollment_id", "source_id", "id")
        )
        exam_clinic_link_map: Dict[tuple, int] = {
            (int(row["source_id"]), int(row["enrollment_id"])): row["id"]
            for row in exam_clinic_link_qs
        }

        # Exam: 차수별 이력(알림톡/드로어 공통) + 성취(1차/최종 통과 분리)
        exam_attempts_by_key: Dict[tuple[int, int], List[Dict[str, Any]]] = {}
        if exam_ids and enrollment_ids:
            for attempt in (
                ExamAttempt.objects
                .filter(exam_id__in=exam_ids, enrollment_id__in=enrollment_ids)
                .order_by("exam_id", "enrollment_id", "attempt_index")
            ):
                exid = int(attempt.exam_id)
                eid = int(attempt.enrollment_id)
                result = result_map.get(exid, {}).get(eid)
                exam_attempts_by_key.setdefault((exid, eid), []).append(
                    _build_exam_attempt_summary(
                        attempt=attempt,
                        result=result,
                        exam_pass_score=exam_pass_score_map.get(exid, 0.0),
                        exam_max_score=exam_max_score_map.get(exid, 100.0),
                    )
                )

        achievement_items: List[Dict[str, Any]] = []
        for exid in exam_ids:
            for eid in enrollment_ids:
                if (eid, exid) not in exam_enrolled_set:
                    continue
                r = result_map.get(exid, {}).get(eid)
                achievement_items.append({
                    "enrollment_id": eid,
                    "exam_id": exid,
                    "total_score": float(r.total_score or 0.0) if r is not None else None,
                    "pass_score": exam_pass_score_map.get(exid, 0.0),
                    "attempt_id": int(r.attempt_id) if r is not None and r.attempt_id else None,
                    "session": session,
                })
        achievement_map = compute_exam_achievement_bulk(
            items=achievement_items,
            use_session_filter=True,
        )

        # Homework: 차수(attempt) 수
        hw_attempt_stats = (
            HomeworkScore.objects
            .filter(
                session=session,
                enrollment_id__in=enrollment_ids,
                homework_id__in=homework_ids,
            )
            .values("homework_id", "enrollment_id")
            .annotate(count=Count("id"))
        )
        hw_attempt_count_map: Dict[tuple, int] = {
            (int(row["homework_id"]), int(row["enrollment_id"])): row["count"]
            for row in hw_attempt_stats
        }

        # Homework: 미해소 ClinicLink ID
        hw_clinic_link_qs = (
            ClinicLink.objects
            .filter(
                session=session,
                enrollment_id__in=clinic_ids,
                source_type="homework",
                source_id__in=homework_ids,
                resolved_at__isnull=True,
            )
            .values("enrollment_id", "source_id", "id")
        )
        hw_clinic_link_map: Dict[tuple, int] = {
            (int(row["source_id"]), int(row["enrollment_id"])): row["id"]
            for row in hw_clinic_link_qs
        }

        # -------------------------------------------------
        # 9) Rows
        # -------------------------------------------------
        rows: List[Dict[str, Any]] = []

        for eid in enrollment_ids:
            progress_completed = eid in progress_completed_ids
            progress_status = "completed" if progress_completed else "in_progress"
            clinic_required = eid in clinic_ids

            exams_payload = []
            exam_updated_ats = []

            for exid in exam_ids:
                # 해당 시험에 미등록이면 스킵 (프론트에서 회색 비활성 셀)
                if (eid, exid) not in exam_enrolled_set:
                    continue

                r = result_map.get(exid, {}).get(eid)

                if r is None:
                    omr_review_meta = omr_review_map.get((exid, eid))
                    block = {
                        "score": None,
                        "max_score": None,
                        "passed": None,
                        "clinic_required": clinic_required,
                        "is_locked": False,
                        "lock_reason": None,
                        "objective_score": None,
                        "subjective_score": None,
                        "meta": omr_review_meta,
                    }
                    updated_at = None
                else:
                    attempt_status = (
                        attempt_status_map.get(int(r.attempt_id), "")
                        if r.attempt_id is not None
                        else ""
                    )
                    locked = attempt_status.lower() == "grading" if attempt_status else False
                    pass_score = exam_pass_score_map.get(exid, 0.0)

                    # ✅ 미응시 감지
                    _meta_status = (
                        attempt_meta_status_map.get(int(r.attempt_id))
                        if r.attempt_id is not None else None
                    )
                    is_not_submitted = (_meta_status == "NOT_SUBMITTED")

                    # 미응시 → passed=None / pass_score=0(기준 미설정) → passed=None
                    if is_not_submitted:
                        passed = None
                    elif pass_score > 0:
                        passed = bool(float(r.total_score or 0.0) >= float(pass_score))
                    else:
                        passed = None

                    items_list = list(r.items.all()) if hasattr(r, "items") else []
                    objective_val = float(getattr(r, "objective_score", 0.0) or 0.0)
                    subjective_val = max(
                        0.0,
                        float(r.total_score or 0.0) - objective_val,
                    )

                    block = {
                        "score": None if is_not_submitted else float(r.total_score or 0.0),
                        "max_score": float(r.max_score or 0.0),
                        "passed": passed,
                        "clinic_required": clinic_required,
                        "is_locked": locked,
                        "lock_reason": "GRADING" if locked else None,
                        "objective_score": None if is_not_submitted else objective_val,
                        "subjective_score": None if is_not_submitted else subjective_val,
                        "meta": {"status": "NOT_SUBMITTED"} if is_not_submitted else None,
                    }
                    updated_at = r.updated_at

                if updated_at:
                    exam_updated_ats.append(updated_at)

                achievement_data = achievement_map.get((eid, exid), {})
                if achievement_data:
                    block["passed"] = achievement_data.get("is_pass")
                    block["remediated"] = achievement_data.get("remediated")
                    block["final_pass"] = achievement_data.get("final_pass")
                    block["achievement"] = achievement_data.get("achievement")
                    block["is_provisional"] = bool(achievement_data.get("is_provisional"))
                    block["clinic_retake"] = achievement_data.get("clinic_retake")
                    if achievement_data.get("meta_status") and not block.get("meta"):
                        block["meta"] = {"status": achievement_data.get("meta_status")}

                items_payload: List[Dict[str, Any]] = []
                if r is not None and hasattr(r, "items"):
                    score_shape = score_shape_by_exam.get(int(exid))
                    for ri in items_list:
                        item_payload = {
                            "question_id": ri.question_id,
                            "score": float(ri.score or 0.0),
                            "max_score": float(ri.max_score or 0.0),
                        }
                        if score_shape is not None:
                            item_payload["question_number"] = score_shape.question_number_by_id.get(int(ri.question_id))
                            item_payload["question_kind"] = score_shape.question_kind(int(ri.question_id))
                        items_payload.append(item_payload)

                exams_payload.append(
                    {
                        "exam_id": exid,
                        "title": exam_title_map.get(exid, ""),
                        "pass_score": exam_pass_score_map.get(exid, 0.0),
                        "block": block,
                        "items": items_payload,
                        "attempt_count": max(
                            exam_attempt_count_map.get((exid, eid), 0),
                            1 if r is not None else 0,
                        ),
                        "clinic_link_id": exam_clinic_link_map.get((exid, eid)),
                        "attempts": exam_attempts_by_key.get((exid, eid), []),
                    }
                )

            homeworks_payload = []
            for hw in homeworks:
                # 해당 과제에 미등록이면 스킵 (프론트에서 회색 비활성 셀)
                if (eid, int(hw.id)) not in hw_assigned_set:
                    continue

                hs = hw_map.get(eid, {}).get(int(hw.id))

                if hs is None:
                    block = {
                        "score": None,
                        "max_score": None,
                        "passed": None,
                        "clinic_required": clinic_required,
                        "is_locked": False,
                        "lock_reason": None,
                        "meta": None,
                    }
                    updated_at = None
                else:
                    block = {
                        "score": hs.score,
                        "max_score": hs.max_score,
                        "passed": (
                            bool(hs.passed)
                            if hs.passed is not None
                            else None
                        ),
                        "clinic_required": clinic_required,
                        "is_locked": bool(hs.is_locked),
                        "lock_reason": hs.lock_reason,
                        "meta": getattr(hs, "meta", None),
                    }
                    updated_at = hs.updated_at

                homeworks_payload.append(
                    {
                        "homework_id": int(hw.id),
                        "title": str(hw.title),
                        "block": block,
                        "attempt_count": hw_attempt_count_map.get((int(hw.id), eid), 0),
                        "clinic_link_id": hw_clinic_link_map.get((int(hw.id), eid)),
                    }
                )

            hw_updated_ats = [
                hs.updated_at
                for hs in (hw_map.get(eid, {}).values())
                if hs.updated_at
            ]
            all_timestamps = [
                *(exam_updated_ats or []),
                *hw_updated_ats,
                getattr(session, "updated_at", None),
            ]
            updated_at = max((d for d in all_timestamps if d), default=None)

            # 클리닉 대상이면서 해당 주차 클리닉 미수강 → 이름만 노란 형광펜 하이라이트(백엔드 단일 진실)
            # 수강 완료(ATTENDED) 시 하이라이트 제거
            name_highlight_clinic_target = (
                clinic_required and eid not in enrollment_ids_clinic_attended
            )

            # 학생 SSOT 표시용 필드 (아바타 + 강의 딱지)
            display = _get_enrollment_display_fields(enrollment_map.get(eid))

            rows.append(
                {
                    "enrollment_id": eid,
                    "student_id": student_id_map.get(eid),
                    "student_name": student_name_map.get(eid, "-"),
                    "exams": exams_payload,
                    "homeworks": homeworks_payload,
                    "updated_at": updated_at or timezone.now(),
                    "clinic_required": clinic_required,
                    "progress_completed": progress_completed,
                    "progress_status": progress_status,
                    "name_highlight_clinic_target": name_highlight_clinic_target,
                    **display,
                }
            )

        return Response(
            {
                "meta": response_meta,
                "rows": SessionScoreRowSerializer(rows, many=True).data,
            }
        )
