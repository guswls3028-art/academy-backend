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
    def list_admin_targets() -> List[Dict[str, Any]]:
        links = (
            ClinicLink.objects.filter(is_auto=True)
            # ✅ 수정사항(추가): 예약 완료로 분리된 대상자는 "대상자"에서 제외
            .filter(resolved_at__isnull=True)
            .select_related("session")
            .order_by("-created_at")  # 최신 자동 대상 우선
        )

        out: List[Dict[str, Any]] = []

        # 세션별 exam 후보 캐시 (쿼리 절약)
        exams_cache: Dict[int, Optional[Exam]] = {}

        for link in links:
            session = getattr(link, "session", None)
            if not session:
                continue

            session_id = int(getattr(session, "id", 0) or 0)
            enrollment_id = int(getattr(link, "enrollment_id", 0) or 0)
            if not session_id or not enrollment_id:
                continue

            # 대표 exam 선정 (세션에 시험이 여러 개면 1개만 표기)
            if session_id not in exams_cache:
                exams = list(get_exams_for_session(session))
                if exams:
                    # ✅ 보수적 정책: id가 가장 작은 exam을 대표로
                    ex = sorted(exams, key=lambda x: int(getattr(x, "id", 0) or 0))[0]
                    exams_cache[session_id] = ex
                else:
                    exams_cache[session_id] = None

            exam = exams_cache.get(session_id)
            if not exam:
                # 세션에 시험이 없으면 score/cutline은 0으로 내려서 화면이 깨지지 않게
                out.append({
                    "enrollment_id": enrollment_id,
                    "student_name": _get_student_name_by_enrollment_id(enrollment_id),
                    "session_title": _get_session_title(session),
                    "reason": "score",
                    "exam_score": 0.0,
                    "cutline_score": 0.0,
                    "created_at": getattr(link, "created_at", None),
                })
                continue

            exam_id = int(getattr(exam, "id", 0) or 0)
            cutline = _safe_float(getattr(exam, "pass_score", 0.0), 0.0)

            # 대표 스냅샷 Result (시험 단위)
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

            # reason 판정
            # - LOW_CONFIDENCE 흔적이 있으면 confidence
            # - 아니면 score
            reason = "confidence" if _is_low_confidence_for_attempt(
                exam_id=exam_id,
                enrollment_id=enrollment_id,
                attempt_id=attempt_id if attempt_id else None,
            ) else "score"

            out.append({
                "enrollment_id": enrollment_id,
                "student_name": _get_student_name_by_enrollment_id(enrollment_id),
                "session_title": _get_session_title(session),
                "reason": reason,
                "exam_score": float(exam_score),
                "cutline_score": float(cutline),
                "created_at": getattr(link, "created_at", None),
            })

        return out
