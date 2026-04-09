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
# 카카오 검수 승인 완료 시 True로 변경 → 즉시 통합 템플릿 사용 시작
# 미승인 상태에서 True로 두면 Solapi 발송 거부됨 — 반드시 승인 확인 후 변경
UNIFIED_TEMPLATES_ENABLED = True

SOLAPI_CLINIC_INFO = "KA01TP2604061058318608Hy40ZnTFZT"      # 클리닉 일정 안내
SOLAPI_CLINIC_CHANGE = "KA01TP260406110706969XS06XRZveEk"    # 클리닉 일정 변경
SOLAPI_SCORE = "KA01TP260406105458211774JKJ3OU55"            # 성적표발송
SOLAPI_ATTENDANCE = "KA01TP260406121126868FGddLmrDFUC"       # 수업출석안내

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
    # clinic_check_out: clinic_self_study_completed로 통합
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
    """트리거에 해당하는 Solapi 템플릿 ID 반환. 통합 템플릿 미활성 시 None."""
    if not UNIFIED_TEMPLATES_ENABLED:
        return None
    tt = get_template_type(trigger)
    if tt:
        return TEMPLATE_TYPE_TO_SOLAPI_ID.get(tt)
    return None


# ──────────────────────────────────────────
# 카테고리 → 통합 템플릿 타입 매핑 (수동 발송용)
# ──────────────────────────────────────────
# 시스템 기본양식(signup)은 자체 Solapi 템플릿 유지 → 매핑에서 제외.

CATEGORY_TO_TEMPLATE_TYPE: dict[str, str] = {
    "grades": TYPE_SCORE,
    "exam": TYPE_SCORE,
    "assignment": TYPE_SCORE,
    "attendance": TYPE_ATTENDANCE,
    "lecture": TYPE_ATTENDANCE,
    "clinic": TYPE_CLINIC_INFO,
    "payment": TYPE_SCORE,
    "notice": TYPE_SCORE,
    "community": TYPE_SCORE,
    "staff": TYPE_SCORE,
    "default": TYPE_SCORE,
    "student": TYPE_SCORE,
}

# 시스템 기본양식 — 통합 4종 대신 자체 Solapi 템플릿 유지
SYSTEM_TEMPLATE_CATEGORIES = frozenset({"signup"})


def get_unified_for_category(
    category: str,
    template_name: str = "",
    extra_vars: dict | None = None,
) -> tuple[str | None, str | None]:
    """
    카테고리에 해당하는 통합 템플릿 (template_type, solapi_id) 반환.
    시스템 기본양식(signup) 또는 통합 미활성 시 (None, None).

    clinic 카테고리는 template_name 또는 extra_vars로 clinic_info/clinic_change 구분:
    - "변경"/"취소" 키워드 → clinic_change
    - 클리닉기존일정/클리닉변동사항 변수 존재 → clinic_change
    - 그 외 → clinic_info
    """
    if not UNIFIED_TEMPLATES_ENABLED:
        return None, None
    if category in SYSTEM_TEMPLATE_CATEGORIES:
        return None, None

    tt = CATEGORY_TO_TEMPLATE_TYPE.get(category)

    # clinic 카테고리: 변경/취소 vs 일반 안내 분류
    if tt == TYPE_CLINIC_INFO:
        is_change = False
        name_lower = (template_name or "").lower()
        if "변경" in name_lower or "취소" in name_lower:
            is_change = True
        # 영문 템플릿명도 변경/취소 계열로 분류
        # 예: clinic change, changed, cancel, cancelled, reschedule, rescheduled
        english_change_keywords = (
            "change",
            "changed",
            "cancel",
            "cancelled",
            "canceled",
            "reschedule",
            "rescheduled",
        )
        if any(k in name_lower for k in english_change_keywords):
            is_change = True
        if extra_vars:
            if extra_vars.get("클리닉기존일정") or extra_vars.get("클리닉변동사항") or extra_vars.get("클리닉수정자"):
                is_change = True
        if is_change:
            tt = TYPE_CLINIC_CHANGE

    if tt:
        return tt, TEMPLATE_TYPE_TO_SOLAPI_ID.get(tt)
    return None, None


def build_manual_replacements(
    template_type: str,
    content_body: str,
    context: dict,
    tenant_name: str,
    student_name: str,
    site_url: str,
) -> list[dict[str, str]]:
    """
    수동 발송용 통합 템플릿 replacements 빌드.
    build_unified_replacements()와 동일 로직이나, trigger 대신 template_type을 직접 받음.
    """
    import re

    # 서브변수 치환용 dict
    all_vars = {
        "학원이름": tenant_name,
        "학원명": tenant_name,
        "학생이름": student_name,
        "학생이름2": student_name[-2:] if len(student_name) >= 2 else student_name,
        "학생이름3": student_name,
        "사이트링크": site_url,
    }
    context_var_mapping: dict[str, list[str]] = {
        "클리닉장소": ["장소", "클리닉장소", "place"],
        "클리닉날짜": ["날짜", "클리닉날짜", "date"],
        "클리닉시간": ["시간", "클리닉시간", "time"],
        "클리닉기존일정": ["클리닉기존일정", "clinic_old_schedule"],
        "클리닉변동사항": ["클리닉변동사항", "clinic_changes"],
        "클리닉수정자": ["클리닉수정자", "clinic_modifier"],
        "강의명": ["강의명", "lecture_name"],
        "차시명": ["차시명", "session_name"],
        "강의날짜": ["날짜", "강의날짜", "date"],
        "강의시간": ["시간", "강의시간", "time"],
        "날짜": ["날짜", "date"],
        "시간": ["시간", "time"],
    }

    for var_name, ctx_keys in context_var_mapping.items():
        for ctx_key in ctx_keys:
            if ctx_key in context and context[ctx_key]:
                all_vars[var_name] = str(context[ctx_key])
                break

    for k, v in context.items():
        if not k.startswith("_") and k not in all_vars:
            all_vars[k] = str(v) if v else ""

    # content_body 내 #{서브변수} 치환 → #{선생님메모} 값
    built_content = content_body
    for k, v in all_vars.items():
        built_content = built_content.replace(f"#{{{k}}}", v)

    built_content = re.sub(r"#\{[^}]+\}", "", built_content)
    built_content = re.sub(r"\n{3,}", "\n\n", built_content).strip()

    # ── 선생님메모에 컨텍스트 자동 보강 (build_unified_replacements와 동일 로직) ──
    memo_value = built_content
    _ESSENTIAL: dict[str, list[tuple[str, str]]] = {
        TYPE_CLINIC_INFO: [("학생이름", student_name), ("클리닉장소", all_vars.get("클리닉장소", "")), ("클리닉날짜", all_vars.get("클리닉날짜", "")), ("클리닉시간", all_vars.get("클리닉시간", ""))],
        TYPE_CLINIC_CHANGE: [("학생이름", student_name), ("클리닉기존일정", all_vars.get("클리닉기존일정", "")), ("클리닉변동사항", all_vars.get("클리닉변동사항", ""))],
        TYPE_ATTENDANCE: [("학생이름", student_name), ("강의명", all_vars.get("강의명", "")), ("강의날짜", all_vars.get("강의날짜", "")), ("강의시간", all_vars.get("강의시간", ""))],
        TYPE_SCORE: [("학생이름", student_name), ("강의명", all_vars.get("강의명", "")), ("차시명", all_vars.get("차시명", ""))],
    }
    _ess = _ESSENTIAL.get(template_type, [])
    _has_var = any(f"#{{{vn}}}" in content_body for vn, _ in _ess)
    if not _has_var and _ess:
        _parts = []
        if student_name:
            _parts.append(f"{student_name} 학생 안내드립니다.")
        _VAR_LBL = {"클리닉장소": "장소", "클리닉날짜": "날짜", "클리닉시간": "시간", "클리닉기존일정": "기존일정", "클리닉변동사항": "변동사항", "강의명": "강의", "차시명": "차시", "강의날짜": "날짜", "강의시간": "시간"}
        _details = [f"{_VAR_LBL.get(vn, vn)}: {vv}" for vn, vv in _ess if vn != "학생이름" and vv]
        if _details:
            _parts.append("\n".join(_details))
        if _parts:
            memo_value = f"{chr(10).join(_parts)}\n\n{built_content}".strip()

    # Solapi replacements
    registered_vars = TEMPLATE_TYPE_VARIABLES.get(template_type, [])
    replacements = []
    for var_name in registered_vars:
        if var_name == "선생님메모":
            replacements.append({"key": var_name, "value": memo_value})
        elif var_name == "사이트링크":
            replacements.append({"key": var_name, "value": site_url})
        elif var_name in all_vars:
            replacements.append({"key": var_name, "value": all_vars[var_name]})
        else:
            replacements.append({"key": var_name, "value": ""})

    return replacements



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
        "학원명": tenant_name,  # alias — 사용자 커스텀 body에서 #{학원명} 사용 가능
        "학생이름": student_name,
        "학생이름2": student_name[-2:] if len(student_name) >= 2 else student_name,  # 성 제외
        "학생이름3": student_name,  # 전체 이름 (하위 호환)
        "사이트링크": site_url,
    }
    # 도메인 컨텍스트 매핑: Solapi 변수명 → [context에서 찾을 키 목록] (한국어 우선, 영어 하위호환)
    # 호출자는 한국어 키(장소, 날짜, 시간 등)를 전달함.
    context_var_mapping: dict[str, list[str]] = {
        # clinic_info — 호출자: {"장소": "301호", "날짜": "2026-04-08", "시간": "14:00"}
        "클리닉장소": ["장소", "place"],
        "클리닉날짜": ["날짜", "date"],
        "클리닉시간": ["시간", "time"],
        "클리닉명": ["클리닉명", "clinic_name"],
        # clinic_change — 호출자: 미구현, 향후 한국어 키 사용 예정
        "클리닉기존일정": ["클리닉기존일정", "clinic_old_schedule"],
        "클리닉변동사항": ["클리닉변동사항", "clinic_changes"],
        "클리닉수정자": ["클리닉수정자", "clinic_modifier"],
        # score / attendance — 호출자: {"강의명": "수학", "차시명": "1차시", "날짜": "...", "시간": "..."}
        "강의명": ["강의명", "lecture_name"],
        "차시명": ["차시명", "session_name"],
        "강의날짜": ["날짜", "date"],
        "강의시간": ["시간", "time"],
        "시험명": ["시험명", "exam_name"],
        "과제명": ["과제명", "assignment_name"],
        "성적": ["성적", "score"],
        "시험성적": ["시험성적", "exam_score"],
        "클리닉합불": ["클리닉합불", "clinic_result"],
        # common
        "날짜": ["날짜", "date"],
        "시간": ["시간", "time"],
        "장소": ["장소", "place"],
    }

    for var_name, ctx_keys in context_var_mapping.items():
        for ctx_key in ctx_keys:
            if ctx_key in context and context[ctx_key]:
                all_vars[var_name] = str(context[ctx_key])
                break

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

    # ── 선생님메모에 컨텍스트 자동 보강 ──
    # Solapi 템플릿 본문 = #{선생님메모}\n#{사이트링크}
    # 선생님메모 값 = template.body의 #{서브변수} 치환 결과.
    #
    # 선생님이 body에 #{학생이름}, #{장소} 등 변수 블록을 직접 써서 커스텀 가능.
    # 하지만 body가 변수 없이 작성된 경우("클리닉 안내 드립니다."), 카카오톡에
    # 학생이름/장소/날짜/시간이 전혀 표시되지 않으므로 —
    # body에 해당 변수가 하나도 포함되지 않았으면, 핵심 정보를 자동 추가한다.
    memo_value = built_content

    # 템플릿 타입별 핵심 변수 — body에 이 변수 중 하나라도 있으면 선생님이 커스텀한 것
    TYPE_ESSENTIAL_VARS: dict[str, list[tuple[str, str]]] = {
        TYPE_CLINIC_INFO: [("학생이름", student_name), ("클리닉장소", all_vars.get("클리닉장소", "")), ("클리닉날짜", all_vars.get("클리닉날짜", "")), ("클리닉시간", all_vars.get("클리닉시간", ""))],
        TYPE_CLINIC_CHANGE: [("학생이름", student_name), ("클리닉기존일정", all_vars.get("클리닉기존일정", "")), ("클리닉변동사항", all_vars.get("클리닉변동사항", ""))],
        TYPE_ATTENDANCE: [("학생이름", student_name), ("강의명", all_vars.get("강의명", "")), ("강의날짜", all_vars.get("강의날짜", "")), ("강의시간", all_vars.get("강의시간", ""))],
        TYPE_SCORE: [("학생이름", student_name), ("강의명", all_vars.get("강의명", "")), ("차시명", all_vars.get("차시명", ""))],
    }

    essential = TYPE_ESSENTIAL_VARS.get(template_type, [])
    body_has_any_var = any(f"#{{{var_name}}}" in content_body for var_name, _ in essential)

    if not body_has_any_var and essential:
        # 선생님이 변수 블록을 하나도 안 썼으므로, 핵심 정보를 자동 앞에 추가
        info_parts = []
        if student_name:
            info_parts.append(f"{student_name} 학생 안내드립니다.")
        detail_lines = []
        # 변수명 → 사람 친화적 라벨
        VAR_LABELS = {
            "클리닉장소": "장소", "클리닉날짜": "날짜", "클리닉시간": "시간",
            "클리닉기존일정": "기존일정", "클리닉변동사항": "변동사항",
            "강의명": "강의", "차시명": "차시", "강의날짜": "날짜", "강의시간": "시간",
        }
        for var_name, var_val in essential:
            if var_name == "학생이름" or not var_val:
                continue
            label = VAR_LABELS.get(var_name, var_name)
            detail_lines.append(f"{label}: {var_val}")
        if detail_lines:
            info_parts.append("\n".join(detail_lines))
        if info_parts:
            header = "\n".join(info_parts)
            memo_value = f"{header}\n\n{built_content}".strip()

    # Solapi replacements 빌드 — 등록된 모든 변수에 값 제공
    registered_vars = TEMPLATE_TYPE_VARIABLES.get(template_type, [])
    replacements = []
    for var_name in registered_vars:
        if var_name == "선생님메모":
            replacements.append({"key": var_name, "value": memo_value})
        elif var_name == "사이트링크":
            replacements.append({"key": var_name, "value": site_url})
        elif var_name in all_vars:
            replacements.append({"key": var_name, "value": all_vars[var_name]})
        else:
            replacements.append({"key": var_name, "value": ""})

    return replacements
