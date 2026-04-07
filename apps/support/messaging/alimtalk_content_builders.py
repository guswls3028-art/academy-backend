# apps/support/messaging/alimtalk_content_builders.py
"""
통합 알림톡 템플릿 — 3개 범용 Solapi 템플릿으로 모든 자동발송 커버.

구조:
  Solapi 템플릿 본문 = "#{선생님메모}\n#{사이트링크}"
  → 백엔드에서 트리거별 메시지를 조립해 #{선생님메모} 값으로 전송
  → #{사이트링크}는 테넌트 URL

트리거별 기본 #{선생님메모} 컨텐츠는 default_templates.py의 body에 정의.
선생님이 편집한 body는 MessageTemplate.body에 저장됨.
"""

from __future__ import annotations

# ──────────────────────────────────────────
# 통합 Solapi 템플릿 ID (검수 통과 후 사용)
# ──────────────────────────────────────────

SOLAPI_CLINIC_INFO = "KA01TP2604061058318608Hy40ZnTFZT"      # 클리닉 일정 안내
SOLAPI_CLINIC_CHANGE = "KA01TP260406110706969XS06XRZveEk"    # 클리닉 일정 변경
SOLAPI_SCORE = "KA01TP260406105458211774JKJ3OU55"            # 성적표발송
SOLAPI_ATTENDANCE = "KA01TP260406121126868FGddLmrDFUC"       # 수업출석안내

UNIFIED_TEMPLATE_IDS = frozenset({
    SOLAPI_CLINIC_INFO,
    SOLAPI_CLINIC_CHANGE,
    SOLAPI_SCORE,
    SOLAPI_ATTENDANCE,
})

# ──────────────────────────────────────────
# 템플릿 타입 상수
# ──────────────────────────────────────────

TYPE_CLINIC_INFO = "clinic_info"        # 장소/날짜/시간
TYPE_CLINIC_CHANGE = "clinic_change"    # 기존일정/변동사항/수정자
TYPE_SCORE = "score"                    # 강의명/차시명
TYPE_ATTENDANCE = "attendance"          # 강의명/차시명/강의날짜/강의시간

TEMPLATE_TYPE_TO_SOLAPI_ID = {
    TYPE_CLINIC_INFO: SOLAPI_CLINIC_INFO,
    TYPE_CLINIC_CHANGE: SOLAPI_CLINIC_CHANGE,
    TYPE_SCORE: SOLAPI_SCORE,
    TYPE_ATTENDANCE: SOLAPI_ATTENDANCE,
}

# ──────────────────────────────────────────
# 트리거 → 템플릿 타입 매핑
# ──────────────────────────────────────────

TRIGGER_TO_TEMPLATE_TYPE: dict[str, str] = {
    # 클리닉 일정 안내 (장소/날짜/시간)
    "clinic_reservation_created": TYPE_CLINIC_INFO,
    "clinic_reminder": TYPE_CLINIC_INFO,
    "clinic_check_in": TYPE_CLINIC_INFO,
    "clinic_check_out": TYPE_CLINIC_INFO,
    "clinic_absent": TYPE_CLINIC_INFO,
    "clinic_self_study_completed": TYPE_CLINIC_INFO,
    "clinic_result_notification": TYPE_CLINIC_INFO,
    "counseling_reservation_created": TYPE_CLINIC_INFO,

    # 클리닉 일정 변경 (기존일정/변동사항/수정자)
    "clinic_reservation_changed": TYPE_CLINIC_CHANGE,
    "clinic_cancelled": TYPE_CLINIC_CHANGE,

    # 수업출석안내 (강의명/차시명/강의날짜/강의시간)
    "check_in_complete": TYPE_ATTENDANCE,
    "absent_occurred": TYPE_ATTENDANCE,
    "lecture_session_reminder": TYPE_ATTENDANCE,

    # 성적표발송 (강의명/차시명)
    "exam_scheduled_days_before": TYPE_SCORE,
    "exam_start_minutes_before": TYPE_SCORE,
    "exam_not_taken": TYPE_SCORE,
    "exam_score_published": TYPE_SCORE,
    "retake_assigned": TYPE_SCORE,
    "assignment_registered": TYPE_SCORE,
    "assignment_due_hours_before": TYPE_SCORE,
    "assignment_not_submitted": TYPE_SCORE,
    "monthly_report_generated": TYPE_SCORE,
}


def get_template_type(trigger: str) -> str | None:
    """트리거에 해당하는 통합 템플릿 타입 반환. 매핑 없으면 None."""
    return TRIGGER_TO_TEMPLATE_TYPE.get(trigger)


def get_solapi_template_id(trigger: str) -> str | None:
    """트리거에 해당하는 Solapi 템플릿 ID 반환."""
    tt = get_template_type(trigger)
    if tt:
        return TEMPLATE_TYPE_TO_SOLAPI_ID.get(tt)
    return None


def is_unified_template(solapi_template_id: str) -> bool:
    """통합 템플릿 ID인지 확인."""
    return solapi_template_id in UNIFIED_TEMPLATE_IDS


# ──────────────────────────────────────────
# 템플릿 타입별 등록 변수 (Solapi에 전달해야 하는 전체 변수)
# ──────────────────────────────────────────

TEMPLATE_TYPE_VARIABLES: dict[str, list[str]] = {
    TYPE_CLINIC_INFO: [
        "학원이름", "학생이름", "클리닉장소", "클리닉날짜", "클리닉시간", "선생님메모", "사이트링크",
    ],
    TYPE_CLINIC_CHANGE: [
        "학원이름", "학생이름", "클리닉기존일정", "클리닉변동사항", "클리닉수정자", "선생님메모", "사이트링크",
    ],
    TYPE_SCORE: [
        "학원이름", "학생이름", "강의명", "차시명", "선생님메모", "사이트링크",
    ],
    TYPE_ATTENDANCE: [
        "학원이름", "학생이름", "강의명", "차시명", "강의날짜", "강의시간", "선생님메모", "사이트링크",
    ],
}


def build_unified_replacements(
    trigger: str,
    content_body: str,
    context: dict,
    tenant_name: str,
    student_name: str,
    site_url: str,
) -> list[dict[str, str]]:
    """
    통합 템플릿용 Solapi replacements 빌드.

    1. content_body 내의 #{서브변수}를 context 값으로 치환 → #{선생님메모} 값
    2. 템플릿 타입의 등록 변수 전체를 replacements로 반환

    Args:
        trigger: AutoSendConfig 트리거명
        content_body: 사용자 편집 가능한 #{선생님메모} 콘텐츠 (서브변수 포함)
        context: 도메인 컨텍스트 (강의명, 장소 등)
        tenant_name: 학원명
        student_name: 학생 전체 이름
        site_url: 테넌트 사이트 URL

    Returns:
        Solapi replacements list: [{"key": "선생님메모", "value": "..."}, ...]
    """
    import re

    template_type = get_template_type(trigger)
    if not template_type:
        return []

    # 서브변수 치환용 dict
    all_vars = {
        "학원이름": tenant_name,
        "학생이름": student_name,
        "사이트링크": site_url,
    }
    # 도메인 컨텍스트 매핑
    context_var_mapping = {
        # clinic_info
        "클리닉장소": "place",
        "클리닉날짜": "date",
        "클리닉시간": "time",
        "클리닉명": "clinic_name",
        # clinic_change
        "클리닉기존일정": "clinic_old_schedule",
        "클리닉변동사항": "clinic_changes",
        "클리닉수정자": "clinic_modifier",
        # score / attendance
        "강의명": "lecture_name",
        "차시명": "session_name",
        "강의날짜": "date",
        "강의시간": "time",
        "시험명": "exam_name",
        "과제명": "assignment_name",
        "성적": "score",
        "시험성적": "exam_score",
        "클리닉합불": "clinic_result",
        # common
        "날짜": "date",
        "시간": "time",
        "장소": "place",
    }

    for var_name, ctx_key in context_var_mapping.items():
        if ctx_key in context and context[ctx_key]:
            all_vars[var_name] = str(context[ctx_key])

    # 직접 한국어 키로 전달된 context도 반영
    for k, v in context.items():
        if not k.startswith("_") and k not in all_vars:
            all_vars[k] = str(v) if v else ""

    # content_body 내 #{서브변수} 치환
    built_content = content_body
    for k, v in all_vars.items():
        built_content = built_content.replace(f"#{{{k}}}", v)

    # 미치환 optional 변수 제거
    built_content = re.sub(r"#\{[^}]+\}", "", built_content)
    built_content = re.sub(r"\n{3,}", "\n\n", built_content).strip()

    # Solapi replacements 빌드 — 등록된 모든 변수에 값 제공
    registered_vars = TEMPLATE_TYPE_VARIABLES.get(template_type, [])
    replacements = []
    for var_name in registered_vars:
        if var_name == "선생님메모":
            replacements.append({"key": var_name, "value": built_content})
        elif var_name == "사이트링크":
            replacements.append({"key": var_name, "value": site_url})
        elif var_name in all_vars:
            replacements.append({"key": var_name, "value": all_vars[var_name]})
        else:
            # 등록 변수인데 값이 없으면 빈 문자열 (Solapi 필수)
            replacements.append({"key": var_name, "value": ""})

    return replacements
