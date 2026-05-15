# apps/support/messaging/alimtalk_content_builders.py
# SSOT 문서: backend/docs/domain/messaging-alimtalk.md §0 (학원장 mental model 박스 필독)
"""
통합 알림톡 템플릿 — 4개 범용 Solapi ITEM_LIST 템플릿으로 모든 자동발송 커버.

🚨 학원장 mental model (절대 원칙):
  카카오 검수 통과 4종 양식 = 장식이 다른 4가지 봉투
  #{선생님메모} 한 자리 = 봉투 안에 들어가는 자유 편지 (학원장 무제한 자유 편집)

  선생님이 양식 UI에서 변수블록 추가/제거/문구변경하는 모든 행위 = #{선생님메모} 한 자리 안에서 일어남.
  header/highlight/item.list/prefix는 카카오에 박혀있어 변경 불가 = 봉투의 장식.

  → 새 카테고리 안내 필요 시: 의미 가까운 봉투 선택 + #{선생님메모} 자유 작성. 봉투 새로 만들지 X.
  → "양식이 없다 / 새 템플릿 검수 신청" 추론 영구 금지. 학원장 한 달 반 격분 누적 이력.

구조:
  Solapi 템플릿 본문 = "#{선생님메모}\n#{사이트링크}"
  → 백엔드에서 트리거별 메시지를 조립해 #{선생님메모} 값으로 전송
  → #{사이트링크}는 테넌트 URL

트리거별 기본 #{선생님메모} 컨텐츠는 default_templates.py의 body에 정의.
선생님이 편집한 body는 MessageTemplate.body에 저장됨.

NONE 2종 (notice_withdrawal/notice_payment):
  카카오 등록 본문 자체에 #{선생님메모}/#{공지내용} 자리 미등록 = 학원장 편집 영역 X.
  자동발송 매핑(withdrawal_complete/payment_complete/payment_due_days_before) 유지 = 시스템 안내 자동발송.
  AI가 본문 미반영을 결함으로 분류 + 매핑 제거 = 영구 금지 (2026-05-13 revert 이력).

참조: backend/docs/domain/messaging-alimtalk.md §0, .claude/rules/domain-policy.md §5/§5.5
"""

from __future__ import annotations

# ──────────────────────────────────────────
# 통합 Solapi 템플릿 ID (검수 통과 후 사용)
# ──────────────────────────────────────────
# 카카오 검수 승인 완료 시 True로 변경 → 즉시 통합 템플릿 사용 시작
# 미승인 상태에서 True로 두면 Solapi 발송 거부됨 — 반드시 승인 확인 후 변경
UNIFIED_TEMPLATES_ENABLED = True

# ITEM_LIST 4종 — header/highlight/item.list 자동 렌더, "[성적표 안내]" 등 카테고리 prefix가 카카오 측에 박혀있음
SOLAPI_CLINIC_INFO = "KA01TP2604061058318608Hy40ZnTFZT"      # 클리닉 일정 안내
SOLAPI_CLINIC_CHANGE = "KA01TP260406110706969XS06XRZveEk"    # 클리닉 일정 변경
SOLAPI_SCORE = "KA01TP260406105458211774JKJ3OU55"            # 성적표발송 — 진짜 성적 트리거 전용
SOLAPI_ATTENDANCE = "KA01TP260406121126868FGddLmrDFUC"       # 수업출석안내

# NONE 2종 — emphasizeType=NONE, "안녕하세요, #{학원명}입니다. / #{학생이름2}학생님, ... / ... / #{사이트링크}"
# 카카오 검수 통과 + 살아있는 양식만. (clinic/exam/assignment/generic NONE은 사용자가 정리하며 삭제했음)
SOLAPI_NOTICE_WITHDRAWAL = "KA01TP260324051933087KD2pyRhJonv"   # [HakwonPlus] 퇴원 처리 완료
SOLAPI_NOTICE_PAYMENT    = "KA01TP260324051931785RmT8G8389tA"   # [HakwonPlus] 결제 완료 안내

# ──────────────────────────────────────────
# 템플릿 타입 상수
# ──────────────────────────────────────────

# ITEM_LIST 4종 — 카카오 측에 카테고리 prefix("[성적표 안내]" 등)가 박혀있어 의미 일치 트리거만 사용
TYPE_CLINIC_INFO = "clinic_info"        # 장소/날짜/시간
TYPE_CLINIC_CHANGE = "clinic_change"    # 기존일정/변동사항/수정자
TYPE_SCORE = "score"                    # 강의명/차시명 — 진짜 성적 안내만 ("[성적표 안내]" prefix)
TYPE_ATTENDANCE = "attendance"          # 강의명/차시명/강의날짜/강의시간

# NONE 2종 — 본문 고정형, 사이트링크/학생이름2/학원명만 변수
TYPE_NOTICE_WITHDRAWAL = "notice_withdrawal"
TYPE_NOTICE_PAYMENT    = "notice_payment"

TEMPLATE_TYPE_TO_SOLAPI_ID = {
    TYPE_CLINIC_INFO: SOLAPI_CLINIC_INFO,
    TYPE_CLINIC_CHANGE: SOLAPI_CLINIC_CHANGE,
    TYPE_SCORE: SOLAPI_SCORE,
    TYPE_ATTENDANCE: SOLAPI_ATTENDANCE,
    TYPE_NOTICE_WITHDRAWAL: SOLAPI_NOTICE_WITHDRAWAL,
    TYPE_NOTICE_PAYMENT: SOLAPI_NOTICE_PAYMENT,
}

# NONE 양식 식별 (ITEM_LIST 변수 23자 제한 / item.list 자동 렌더 미적용)
NONE_TEMPLATE_TYPES = frozenset({
    TYPE_NOTICE_WITHDRAWAL,
    TYPE_NOTICE_PAYMENT,
})

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

    # 클리닉 일정 변경/취소
    "clinic_reservation_changed": TYPE_CLINIC_CHANGE,
    "clinic_cancelled": TYPE_CLINIC_CHANGE,

    # 수업출석안내 (강의명/차시명/강의날짜/강의시간)
    "check_in_complete": TYPE_ATTENDANCE,
    "absent_occurred": TYPE_ATTENDANCE,
    "lecture_session_reminder": TYPE_ATTENDANCE,

    # 성적표발송 — 진짜 성적 통보만 (강의명/차시명 + "[성적표 안내]" prefix 의미상 일치)
    "exam_score_published": TYPE_SCORE,
    "monthly_report_generated": TYPE_SCORE,

    # 퇴원 / 결제 — 카테고리별 전용 NONE 양식 (본문 고정 시스템 안내)
    "withdrawal_complete": TYPE_NOTICE_WITHDRAWAL,
    "payment_complete": TYPE_NOTICE_PAYMENT,
    "payment_due_days_before": TYPE_NOTICE_PAYMENT,

    # ── 매핑 의도적 제외 (의미상 맞는 살아있는 봉투 없음) ───────────────
    # NONE 양식은 위 시스템 안내 3종에만 사용한다.
    # 시험/과제/영상/매치업/커뮤니티 트리거는 현행 4종 ITEM_LIST 봉투와 의미가 맞지 않아
    # 자동 통합 매핑을 두지 않는다. 별도 승인 템플릿이 있으면 기존 경로를 사용한다.
    #
    # 영향 트리거: exam_scheduled_days_before / exam_start_minutes_before /
    #   exam_not_taken / retake_assigned / assignment_registered / assignment_due_hours_before /
    #   assignment_not_submitted / video_encoding_complete / matchup_report_submitted /
    #   qna_answered / counsel_answered
    # → 통합 알림톡 비활성. 자체 솔라피 양식 ID가 등록돼 있으면 그쪽 사용, 없으면 발송 차단.
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
    "grades": TYPE_SCORE,                       # 진짜 성적 통보 ("[성적표 안내]" prefix 의미 일치)
    "attendance": TYPE_ATTENDANCE,              # 출결 (강의명/차시명/날짜/시간)
    "lecture": TYPE_ATTENDANCE,                 # 수업
    "clinic": TYPE_CLINIC_INFO,                 # 클리닉 (장소/날짜/시간) — name 변경/취소 시 clinic_change
    "payment": TYPE_NOTICE_PAYMENT,             # 결제/납부 (NONE 양식, 본문 고정 시스템 안내)
    # ── 매핑 의도적 제외 (domain-policy.md §5.5) ─────────────────────
    # exam/assignment/notice/community/staff/default/student: 카카오 등록 양식 부재.
    # 학원장이 명시 요청 시 §5.5 따라 기존 4종 양식 + 본문 변수 재활용으로 확장.
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

    # NONE 양식은 #{공지내용}으로, ITEM_LIST 양식은 #{선생님메모}로 들어감
    all_vars["공지내용"] = built_content
    memo_value = built_content

    # Solapi replacements — ITEM_LIST 변수 23자 제한 (선생님메모/사이트링크/공지내용 제외)
    is_none_type = template_type in NONE_TEMPLATE_TYPES
    registered_vars = TEMPLATE_TYPE_VARIABLES.get(template_type, [])
    replacements = []
    for var_name in registered_vars:
        if var_name == "선생님메모":
            replacements.append({"key": var_name, "value": memo_value})
        elif var_name == "공지내용":
            replacements.append({"key": var_name, "value": built_content})
        elif var_name == "사이트링크":
            replacements.append({"key": var_name, "value": site_url})
        elif var_name in all_vars:
            val = all_vars[var_name]
            if not is_none_type and len(val) > ITEM_LIST_VAR_MAX_LEN:
                val = val[:ITEM_LIST_VAR_MAX_LEN - 1] + "…"
            replacements.append({"key": var_name, "value": val})
        else:
            replacements.append({"key": var_name, "value": "-"})

    return replacements



# ──────────────────────────────────────────
# 템플릿 타입별 등록 변수 (Solapi에 전달해야 하는 전체 변수)
# ──────────────────────────────────────────

# 카카오 검수 시 등록된 변수 전체를 보내야 함 (ITEM_LIST 템플릿).
# 누락하면 3063(잘못된 파라미터), 값이 23자 초과하면 3076(길이초과) 에러.
# 선생님메모에 핵심 정보를 조합하므로 나머지 변수는 요약값만 전달.
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
    # NONE 2종 — 카카오 등록 variables (Solapi 상세 조회로 검증된 set)
    TYPE_NOTICE_WITHDRAWAL: ["학원명", "학생이름2"],                  # 본문 고정, 사이트링크 없음
    TYPE_NOTICE_PAYMENT:    ["학원명", "학생이름2", "사이트링크"],     # 본문 고정 + 사이트링크
}

# ITEM_LIST 변수 값 길이 제한 (카카오 정책: 23자)
ITEM_LIST_VAR_MAX_LEN = 23


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
        # payment — 결제/납부 트리거
        "납부금액": ["납부금액", "amount"],
        "청구월": ["청구월", "billing_month"],
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

    # NONE 양식은 #{공지내용}으로, ITEM_LIST 양식은 #{선생님메모}로 들어감
    all_vars["공지내용"] = built_content
    memo_value = built_content

    # Solapi replacements 빌드 — 등록된 모든 변수에 값 제공
    # ITEM_LIST 변수는 23자 이하로 잘라야 함 (선생님메모/사이트링크/공지내용 제외)
    is_none_type = template_type in NONE_TEMPLATE_TYPES
    registered_vars = TEMPLATE_TYPE_VARIABLES.get(template_type, [])
    replacements = []
    for var_name in registered_vars:
        if var_name == "선생님메모":
            replacements.append({"key": var_name, "value": memo_value})
        elif var_name == "공지내용":
            replacements.append({"key": var_name, "value": built_content})
        elif var_name == "사이트링크":
            replacements.append({"key": var_name, "value": site_url})
        elif var_name in all_vars:
            val = all_vars[var_name]
            if not is_none_type and len(val) > ITEM_LIST_VAR_MAX_LEN:
                val = val[:ITEM_LIST_VAR_MAX_LEN - 1] + "…"
            replacements.append({"key": var_name, "value": val})
        else:
            replacements.append({"key": var_name, "value": "-"})

    return replacements
