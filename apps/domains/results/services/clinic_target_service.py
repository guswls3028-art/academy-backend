# PATH: apps/domains/results/services/clinic_target_service.py
"""
역할
- Admin/Teacher용 "클리닉 대상자" 리스트를 생성한다.
- clinic_required의 단일 진실: progress.ClinicLink(is_auto=True)

설계 계약 (중요)
1) 단일 진실: enrollment_id (학생 식별은 enrollment_id로만)
2) clinic_required 판단은 ClinicLink (자동 트리거) 기준
3) 점수/커트라인/사유(reason)는 results/exams에서 파생
4) Session ↔ Exam 매핑은 results.utils.session_exam.get_exams_for_session() 단일 진실 사용

⚠️ 현실적 제약 (보류/명시)
- "세션에 시험이 여러 개"인 구조에서, ClinicTarget의 exam_score/cutline_score는 1개 숫자만 담는다.
  따라서 본 서비스는 "대표 exam"을 1개 선정해서 표기한다.
  - 기본 정책: get_exams_for_session(session) 중 id가 가장 작은 exam을 대표로 사용
  - 향후 정책 필요 시: ProgressPolicy(strategy)나 운영 규칙에 따라 변경 가능
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from apps.domains.lectures.models import Session
from apps.domains.progress.models import ClinicLink
from apps.domains.exams.models import Exam
from apps.domains.results.models import Result, ResultFact, ExamAttempt
from apps.domains.homework_results.models import HomeworkScore, Homework

# ✅ 단일 진실 유틸
from apps.domains.results.utils.session_exam import get_exams_for_session


def _safe_str(v: Any, default: str = "-") -> str:
    try:
        s = str(v)
        return s if s.strip() else default
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _get_student_photo_url(student: Any) -> Optional[str]:
    """R2 presigned URL 생성 (StudentListSerializer.get_profile_photo_url과 동일 로직)"""
    if not student:
        return None
    r2_key = getattr(student, "profile_photo_r2_key", None) or ""
    if not r2_key:
        return None
    try:
        from django.conf import settings
        from libs.r2_client.presign import create_presigned_get_url
        return create_presigned_get_url(r2_key, expires_in=3600, bucket=settings.R2_STORAGE_BUCKET)
    except Exception:
        return None


def _extract_invalid_reason_from_meta(meta: Any) -> Optional[str]:
    """
    ResultFact.meta / Attempt.meta / SubmissionAnswer.meta 등 다양한 위치에 있을 수 있는
    invalid_reason을 방어적으로 추출한다.

    기대 가능한 형태:
    meta = { "grading": { "invalid_reason": "LOW_CONFIDENCE" } }
    """
    if not isinstance(meta, dict):
        return None
    grading = meta.get("grading")
    if isinstance(grading, dict):
        v = grading.get("invalid_reason")
        return str(v) if v else None
    return None


def _is_low_confidence_for_attempt(*, exam_id: int, enrollment_id: int, attempt_id: Optional[int]) -> bool:
    """
    "신뢰도 낮음" 판정은 프로젝트 구현에 따라:
    - Attempt.meta.grading.invalid_reason (가능)
    - ResultFact.meta.grading.invalid_reason (가능, 현재 grader는 문항 meta에 심는 형태)
    둘 다 방어적으로 체크한다.
    """
    # 1) Attempt.meta (있으면 최우선)
    if attempt_id:
        a = ExamAttempt.objects.filter(id=int(attempt_id)).first()
        if a and hasattr(a, "meta"):
            reason = _extract_invalid_reason_from_meta(getattr(a, "meta", None))
            if (reason or "").upper() == "LOW_CONFIDENCE":
                return True

    # 2) ResultFact.meta (대표 attempt 기준)
    if attempt_id:
        qs = (
            ResultFact.objects.filter(
                target_type="exam",
                target_id=int(exam_id),
                enrollment_id=int(enrollment_id),
                attempt_id=int(attempt_id),
            )
            .exclude(meta__isnull=True)
            .order_by("-id")[:200]  # 방어: 너무 큰 scan 방지
        )
        for f in qs:
            r = _extract_invalid_reason_from_meta(getattr(f, "meta", None))
            if (r or "").upper() == "LOW_CONFIDENCE":
                return True

    return False


def _get_student_name_by_enrollment_id(enrollment_id: int) -> str:
    """
    enrollment_id → student_name 매핑은 프로젝트마다 도메인이 다를 수 있어 방어적으로 구현.

    우선순위:
    1) enrollments.SessionEnrollment (session-enrollments) 모델이 있으면 student_name 필드/조인 사용
    2) enrollment.Enrollment 모델이 있으면 student/user 조인 시도
    3) 실패 시 "-"
    """
    enrollment_id = int(enrollment_id)

    # 1) SessionEnrollment (있으면 가장 확실)
    try:
        # 프로젝트에 따라 앱 경로가 다를 수 있음
        # - apps.domains.enrollments.models.SessionEnrollment (가장 흔함)
        # - apps.domains.enrollments.models.session_enrollment.SessionEnrollment 등
        from apps.domains.enrollments.models import SessionEnrollment  # type: ignore

        se = (
            SessionEnrollment.objects.filter(enrollment_id=enrollment_id)
            .order_by("-id")
            .first()
        )
        if se:
            # serializer 응답에 student_name이 있다고 했던 스펙과 정합성
            v = getattr(se, "student_name", None)
            if v:
                return _safe_str(v, "-")

            # 조인이 가능하면 student.name
            st = getattr(se, "student", None)
            if st and hasattr(st, "name"):
                return _safe_str(getattr(st, "name", None), "-")
    except Exception:
        pass

    # 2) Enrollment (기존 results 코드에서 사용 중)
    try:
        from apps.domains.enrollment.models import Enrollment  # type: ignore

        e = Enrollment.objects.filter(id=enrollment_id).select_related().first()
        if not e:
            return "-"

        # student FK가 있으면 우선
        st = getattr(e, "student", None)
        if st and hasattr(st, "name"):
            return _safe_str(getattr(st, "name", None), "-")

        # user가 학생 프로필을 들고 있을 수도
        u = getattr(e, "user", None)
        if u:
            nm = getattr(u, "name", None) or getattr(u, "username", None)
            return _safe_str(nm, "-")
    except Exception:
        pass

    return "-"


def _get_session_title(session: Session) -> str:
    """
    세션 타이틀은 프로젝트마다 표현이 달라서:
    - __str__ 우선
    - lecture/title/order 등 후보를 방어적으로 조합
    """
    s = _safe_str(session, "-")
    if s != "-" and s.lower() != "session object":
        return s

    lecture = getattr(session, "lecture", None)
    lecture_title = _safe_str(getattr(lecture, "title", None), "")
    order = getattr(session, "order", None)

    if lecture_title and order is not None:
        return f"{lecture_title} {int(order)}회차"
    if lecture_title:
        return lecture_title

    return f"Session#{int(getattr(session, 'id', 0) or 0)}"


@dataclass(frozen=True)
class ClinicTargetRow:
    enrollment_id: int
    student_name: str
    session_title: str
    reason: str  # "score" | "confidence"
    exam_score: float
    cutline_score: float
    created_at: Any


class ClinicTargetService:
    """
    Admin Clinic Targets

    단일 진실:
    - 대상자 모수: ClinicLink(is_auto=True)
    - enrollment_id 기준
    """

    @staticmethod
    def list_admin_targets(tenant: Any = None, include_resolved: bool = False) -> List[Dict[str, Any]]:
        links = (
            ClinicLink.objects.filter(is_auto=True)
            .select_related("session", "session__lecture")
            .order_by("-created_at")
        )
        if not include_resolved:
            links = links.filter(resolved_at__isnull=True)
        if tenant is not None:
            links = links.filter(session__lecture__tenant=tenant)

        # ✅ 퇴원(INACTIVE) 수강생의 ClinicLink 제외
        links = links.filter(enrollment__status="ACTIVE")

        # ✅ enrollment 일괄 조회 (N+1 방지 + 학생 SSOT 표시 필드)
        links_list = list(links)
        all_enrollment_ids = list({int(getattr(lk, "enrollment_id", 0) or 0) for lk in links_list} - {0})
        from apps.domains.enrollment.models import Enrollment as _Enrollment
        enrollment_map: Dict[int, Any] = {
            int(e.id): e
            for e in _Enrollment.objects.filter(id__in=all_enrollment_ids).select_related("student", "lecture")
        }

        # ✅ 클리닉 하이라이트 (미출석 대상자 노란 형광펜)
        from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map
        highlight_map = compute_clinic_highlight_map(
            tenant=tenant,
            enrollment_ids=set(all_enrollment_ids),
        ) if tenant else {}

        out: List[Dict[str, Any]] = []

        # 세션별 exam 후보 캐시 (쿼리 절약)
        exams_cache: Dict[int, Optional[Exam]] = {}

        for link in links_list:
            session = getattr(link, "session", None)
            if not session:
                continue

            session_id = int(getattr(session, "id", 0) or 0)
            enrollment_id = int(getattr(link, "enrollment_id", 0) or 0)
            if not session_id or not enrollment_id:
                continue

            # clinic_reason 판정
            source_type = getattr(link, "source_type", None)
            clinic_reason = source_type or "exam"

            lecture = getattr(session, "lecture", None)
            lecture_id = int(getattr(session, "lecture_id", 0) or 0)
            lecture_title = _safe_str(getattr(lecture, "title", None), "") if lecture else ""

            # 학생 이름: enrollment_map에서 일괄 조회 (N+1 방지)
            enr = enrollment_map.get(enrollment_id)
            student = getattr(enr, "student", None) if enr else None
            if student:
                student_name = _safe_str(getattr(student, "name", None), "-")
            else:
                student_name = _get_student_name_by_enrollment_id(enrollment_id)

            # 학생 프로필 필드 (ClinicTargetSelectModal 테이블 컬럼용)
            parent_phone = getattr(student, "parent_phone", None) if student else None
            student_phone = getattr(student, "phone", None) if student else None
            school_type = getattr(student, "school_type", "HIGH") if student else "HIGH"
            if school_type == "ELEMENTARY":
                school_name = getattr(student, "elementary_school", None) if student else None
            elif school_type == "HIGH":
                school_name = getattr(student, "high_school", None) if student else None
            else:
                school_name = getattr(student, "middle_school", None) if student else None
            grade_val = getattr(student, "grade", None) if student else None
            profile_photo_url = _get_student_photo_url(student)

            # 공통 base row
            base_row = {
                "enrollment_id": enrollment_id,
                "_session_id": session_id,
                "session_id": session_id,
                "lecture_id": lecture_id,
                "lecture_title": lecture_title,
                "lecture_color": getattr(lecture, "color", None) if lecture else None,
                "lecture_chip_label": getattr(lecture, "chip_label", None) if lecture else None,
                "clinic_link_id": int(link.id),
                "cycle_no": int(getattr(link, "cycle_no", 1) or 1),
                "resolution_type": getattr(link, "resolution_type", None),
                "resolved_at": getattr(link, "resolved_at", None),
                "student_name": student_name,
                "session_title": _get_session_title(session),
                "source_type": source_type,
                "source_id": getattr(link, "source_id", None),
                "created_at": getattr(link, "created_at", None),
                "name_highlight_clinic_target": highlight_map.get(enrollment_id, False),
                "parent_phone": parent_phone or "",
                "student_phone": student_phone or "",
                "school": school_name or "",
                "grade": grade_val,
                "profile_photo_url": profile_photo_url,
            }

            # ── Homework source ──
            if source_type == "homework":
                source_id = getattr(link, "source_id", None)
                hw = Homework.objects.filter(id=int(source_id)).first() if source_id else None
                hw_title = _safe_str(getattr(hw, "title", None), "-") if hw else "-"

                # 1차 점수 (성적 산출 대상)
                first_hw_score = HomeworkScore.objects.filter(
                    enrollment_id=enrollment_id,
                    session_id=session_id,
                    homework_id=int(source_id) if source_id else 0,
                    attempt_index=1,
                ).first()

                original_score = float(first_hw_score.score or 0) if first_hw_score and first_hw_score.score is not None else None
                hw_max_score = float(first_hw_score.max_score or 100) if first_hw_score and first_hw_score.max_score else 100.0

                # 과제 cutline은 HomeworkPolicy 기반
                from apps.domains.homework.models import HomeworkPolicy
                hw_tenant = getattr(lecture, "tenant", None) if lecture else None
                cutline = 80.0
                if hw_tenant:
                    hp = HomeworkPolicy.objects.filter(tenant=hw_tenant, session=session).first()
                    if hp:
                        cutline = float(getattr(hp, "cutline_value", 80) or 80)

                # 재시도 이력
                all_hw_scores = HomeworkScore.objects.filter(
                    enrollment_id=enrollment_id,
                    session_id=session_id,
                    homework_id=int(source_id) if source_id else 0,
                ).order_by("attempt_index")

                attempt_history = []
                latest_attempt_index = 1
                for hs in all_hw_scores:
                    attempt_history.append({
                        "attempt_index": hs.attempt_index,
                        "score": float(hs.score) if hs.score is not None else None,
                        "max_score": float(hs.max_score) if hs.max_score is not None else None,
                        "passed": bool(hs.passed),
                        "at": hs.created_at.isoformat() if hs.created_at else None,
                    })
                    latest_attempt_index = max(latest_attempt_index, hs.attempt_index)

                out.append({
                    **base_row,
                    "exam_id": None,
                    "reason": "score",
                    "clinic_reason": "homework",
                    "exam_score": original_score,
                    "cutline_score": float(cutline),
                    "max_score": hw_max_score,
                    "source_title": hw_title,
                    "latest_attempt_index": latest_attempt_index,
                    "attempt_history": attempt_history,
                })
                continue

            # ── Exam source (기존 로직 + 확장) ──
            source_id = getattr(link, "source_id", None)
            if source_type == "exam" and source_id:
                exam = Exam.objects.filter(id=int(source_id)).first()
            else:
                # Legacy fallback: 세션의 대표 exam
                if session_id not in exams_cache:
                    exams = list(get_exams_for_session(session))
                    exams_cache[session_id] = sorted(exams, key=lambda x: x.id)[0] if exams else None
                exam = exams_cache.get(session_id)

            if not exam:
                out.append({
                    **base_row,
                    "exam_id": None,
                    "reason": "score",
                    "clinic_reason": clinic_reason,
                    "exam_score": 0.0,
                    "cutline_score": 0.0,
                    "max_score": 0.0,
                    "source_title": "-",
                    "latest_attempt_index": 1,
                    "attempt_history": [],
                })
                continue

            exam_id = int(getattr(exam, "id", 0) or 0)
            cutline = _safe_float(getattr(exam, "pass_score", 0.0), 0.0)
            exam_max_score = _safe_float(getattr(exam, "max_score", 100.0), 100.0)
            exam_title = _safe_str(getattr(exam, "title", None), "-")

            # 대표 스냅샷 Result (1차 시험 결과 = 성적 산출 대상)
            result = (
                Result.objects.filter(
                    target_type="exam",
                    target_id=exam_id,
                    enrollment_id=enrollment_id,
                )
                .order_by("-id")
                .first()
            )

            exam_score = _safe_float(getattr(result, "total_score", 0.0) if result else 0.0, 0.0)
            attempt_id = int(getattr(result, "attempt_id", 0) or 0) if result else 0

            # 재시도 이력 (ExamAttempt 전체)
            all_attempts = ExamAttempt.objects.filter(
                exam_id=exam_id,
                enrollment_id=enrollment_id,
            ).order_by("attempt_index")

            attempt_history = []
            latest_attempt_index = 1
            for att in all_attempts:
                att_score = None
                att_passed = False
                meta = att.meta or {}
                if "total_score" in meta:
                    att_score = float(meta["total_score"])
                    att_passed = att_score >= cutline if cutline > 0 else False
                elif att.attempt_index == 1 and result:
                    att_score = float(exam_score)
                    att_passed = att_score >= cutline if cutline > 0 else False

                attempt_history.append({
                    "attempt_index": att.attempt_index,
                    "score": att_score,
                    "max_score": exam_max_score,
                    "passed": att_passed,
                    "at": att.created_at.isoformat() if att.created_at else None,
                })
                latest_attempt_index = max(latest_attempt_index, att.attempt_index)

            # reason 판정
            reason = "confidence" if _is_low_confidence_for_attempt(
                exam_id=exam_id,
                enrollment_id=enrollment_id,
                attempt_id=attempt_id if attempt_id else None,
            ) else "score"

            out.append({
                **base_row,
                "exam_id": exam_id,
                "reason": reason,
                "clinic_reason": clinic_reason,
                "exam_score": float(exam_score),
                "cutline_score": float(cutline),
                "max_score": exam_max_score,
                "source_title": exam_title,
                "latest_attempt_index": latest_attempt_index,
                "attempt_history": attempt_history,
            })

        # 미응시(missing)는 클리닉 대상이 아님 — 시험/과제를 아직 응시하지 않은 학생은
        # 불합격한 것이 아니므로 클리닉 보강 대상에서 제외한다.

        return out

    @staticmethod
    def _find_missing_students(
        *,
        tenant: Any,
        exams_cache: Dict[int, Optional[Exam]],
        existing_enrollment_session_pairs: set,
    ) -> List[Dict[str, Any]]:
        """
        누락자 감지: 수강 중이지만 시험 결과가 없는 학생.
        - 시험이 이미 실시된(최소 1명의 결과 존재) 차시만 대상
        - 해당 강의에 ACTIVE 수강 중인 학생 중 Result가 없는 학생을 반환
        """
        import datetime
        from django.utils import timezone
        from apps.domains.enrollment.models import Enrollment

        if tenant is None:
            return []

        out: List[Dict[str, Any]] = []

        # 최근 90일 내 세션만 대상 (성능 제한)
        cutoff = timezone.localdate() - datetime.timedelta(days=90)

        # 시험이 있는 세션 수집
        sessions_with_exams: Dict[int, tuple] = {}  # session_id → (session, exam)
        for session in (
            Session.objects
            .filter(lecture__tenant=tenant, date__gte=cutoff)
            .select_related("lecture")
        ):
            sid = session.id
            if sid in exams_cache:
                exam = exams_cache[sid]
            else:
                exams = list(get_exams_for_session(session))
                exam = sorted(exams, key=lambda x: x.id)[0] if exams else None
                exams_cache[sid] = exam
            if exam:
                sessions_with_exams[sid] = (session, exam)

        if not sessions_with_exams:
            return out

        # 시험이 실시되었는지 확인 (최소 1명의 Result 존재)
        exam_ids = list({exam.id for _, exam in sessions_with_exams.values()})
        exams_with_results = set(
            Result.objects.filter(
                target_type="exam",
                target_id__in=exam_ids,
            ).values_list("target_id", flat=True).distinct()
        )

        # 실시된 시험이 있는 세션만 대상
        active_sessions = {
            sid: (session, exam)
            for sid, (session, exam) in sessions_with_exams.items()
            if exam.id in exams_with_results
        }
        if not active_sessions:
            return out

        # 강의별 ACTIVE 수강생 일괄 조회
        lecture_ids = list({session.lecture_id for session, _ in active_sessions.values()})
        enrollments_by_lecture: Dict[int, set] = {}
        for e in Enrollment.objects.filter(
            lecture_id__in=lecture_ids, tenant=tenant, status="ACTIVE"
        ).values("id", "lecture_id"):
            enrollments_by_lecture.setdefault(e["lecture_id"], set()).add(e["id"])

        # 시험별 결과가 있는 수강생 일괄 조회
        results_by_exam: Dict[int, set] = {}
        for exam_id_val, eid in (
            Result.objects.filter(
                target_type="exam",
                target_id__in=[exam.id for _, exam in active_sessions.values()],
            ).values_list("target_id", "enrollment_id")
        ):
            results_by_exam.setdefault(exam_id_val, set()).add(eid)

        # 기존 ClinicLink가 있는 (session, enrollment) 쌍 일괄 조회
        existing_links = set(
            ClinicLink.objects.filter(
                session_id__in=active_sessions.keys(),
            ).values_list("session_id", "enrollment_id")
        )

        # 누락자 수집
        for sid, (session, exam) in active_sessions.items():
            enrolled_ids = enrollments_by_lecture.get(session.lecture_id, set())
            with_results = results_by_exam.get(exam.id, set())
            cutline = _safe_float(getattr(exam, "pass_score", 0.0), 0.0)

            for eid in enrolled_ids:
                if eid in with_results:
                    continue
                if (sid, eid) in existing_links:
                    continue
                if (eid, sid) in existing_enrollment_session_pairs:
                    continue

                out.append({
                    "enrollment_id": eid,
                    "student_name": _get_student_name_by_enrollment_id(eid),
                    "session_title": _get_session_title(session),
                    "reason": "missing",
                    "clinic_reason": "exam",
                    "exam_score": None,
                    "cutline_score": float(cutline),
                    "max_score": float(getattr(exam, "max_score", 100.0) or 100.0),
                    "source_type": "exam",
                    "source_id": int(exam.id),
                    "source_title": _safe_str(getattr(exam, "title", None), "-"),
                    "lecture_title": _safe_str(
                        getattr(getattr(session, "lecture", None), "title", None), ""
                    ),
                    "lecture_color": getattr(getattr(session, "lecture", None), "color", None),
                    "lecture_chip_label": getattr(getattr(session, "lecture", None), "chip_label", None),
                    "latest_attempt_index": 0,
                    "attempt_history": [],
                    "created_at": None,
                })

        return out
