# SSOT 문서: backend/.claude/domains/messaging.md (수정 시 문서도 동기화)
"""
트리거별 기본 템플릿 정의.
테넌트가 처음 자동발송 설정에 접근할 때 자동 생성됨.

변수명은 카카오 알림톡 검수 통과를 위해 한글로 통일.
{academy_name} 플레이스홀더는 프로비저닝 시 tenant.name으로 치환됨.
"""

import copy


# trigger -> { category, name, subject, body, minutes_before }
_TEMPLATE_DEFINITIONS: dict[str, dict] = {
    # ───────── 가입/등록 ─────────
    "registration_approved_student": {
        "category": "signup",
        "name": "[{academy_name}] 가입 승인 안내 (학생)",
        "subject": "가입 신청이 승인되었습니다",
        "body": (
            "#{학생이름}학생님, 가입 신청이 승인되었습니다.\n"
            "\n"
            "▶ 로그인 정보\n"
            "아이디: #{학생아이디}\n"
            "비밀번호: #{학생비밀번호}\n"
            "\n"
            "▶ 접속 링크\n"
            "#{사이트링크}\n"
            "\n"
            "#{비밀번호안내}\n"
            "첫 로그인 후 비밀번호를 반드시 변경해 주세요."
        ),
        "minutes_before": None,
    },
    "registration_approved_parent": {
        "category": "signup",
        "name": "[{academy_name}] 가입 승인 안내 (학부모)",
        "subject": "학부모 계정이 승인되었습니다",
        "body": (
            "#{학생이름}학생 학부모님, 안녕하세요.\n"
            "가입 신청이 승인되었습니다.\n"
            "\n"
            "▶ 학부모 로그인 정보\n"
            "아이디: #{학부모아이디}\n"
            "비밀번호: #{학부모비밀번호}\n"
            "\n"
            "▶ 학생 로그인 정보\n"
            "아이디: #{학생아이디}\n"
            "비밀번호: #{학생비밀번호}\n"
            "\n"
            "▶ 접속 링크\n"
            "#{사이트링크}\n"
            "\n"
            "#{비밀번호안내}\n"
            "자녀의 수업·성적·출결을 앱에서 확인하실 수 있습니다."
        ),
        "minutes_before": None,
    },
    "withdrawal_complete": {
        "category": "signup",
        "name": "[{academy_name}] 퇴원 처리 완료",
        "subject": "퇴원 처리가 완료되었습니다",
        "body": (
            "퇴원 처리가 완료되었습니다.\n"
            "\n"
            "그동안 학원을 이용해 주셔서 감사합니다.\n"
            "재등록을 원하시면 언제든 문의해 주세요."
        ),
        "minutes_before": None,
    },
    # ───────── 출결 (통합 알림톡: score 템플릿) ─────────
    # body = #{선생님메모} 변수에만 들어가는 안내 문구.
    # 나머지(학원이름, 학생이름, 강의명, 차시명, 사이트링크)는 솔라피 템플릿 하드코딩.
    # ITEM_LIST(attendance) 템플릿: header(학원이름), highlight(학생이름), item.list(강의명/차시명/날짜/시간)
    # body(선생님메모)에 중복 넣지 않음.
    "lecture_session_reminder": {
        "category": "attendance",
        "name": "[{academy_name}] 수업 시작 알림",
        "subject": "오늘 수업이 곧 시작됩니다",
        "body": "오늘 수업이 곧 시작됩니다.\n준비물을 챙기고 시간에 맞춰 입실해 주세요.",
        "minutes_before": 30,
    },
    "check_in_complete": {
        "category": "attendance",
        "name": "[{academy_name}] 입실 완료 알림",
        "subject": "학생이 입실하였습니다",
        "body": "학원에 입실하였습니다.",
        "minutes_before": None,
    },
    "absent_occurred": {
        "category": "attendance",
        "name": "[{academy_name}] 결석 발생 알림",
        "subject": "결석이 발생하였습니다",
        "body": "수업에 결석하였습니다.\n사유가 있으시면 학원으로 연락 부탁드립니다.",
        "minutes_before": None,
    },
    # ITEM_LIST(score) 템플릿: header(학원이름), highlight(학생이름), item.list(강의명/차시명)
    "exam_scheduled_days_before": {
        "category": "exam",
        "name": "[{academy_name}] 시험 예정 안내",
        "subject": "시험이 예정되어 있습니다",
        "body": "시험이 예정되어 있습니다.\n미리 준비해 주세요.",
        "minutes_before": 1440,
    },
    "exam_start_minutes_before": {
        "category": "exam",
        "name": "[{academy_name}] 시험 시작 알림",
        "subject": "시험이 곧 시작됩니다",
        "body": "시험이 곧 시작됩니다.",
        "minutes_before": 30,
    },
    "exam_not_taken": {
        "category": "exam",
        "name": "[{academy_name}] 시험 미응시 알림",
        "subject": "시험에 응시하지 않았습니다",
        "body": "예정된 시험에 아직 응시하지 않았습니다.\n응시 기한 내에 반드시 시험을 완료해 주세요.",
        "minutes_before": None,
    },
    "exam_score_published": {
        "category": "grades",
        "name": "[{academy_name}] 성적 공개 안내",
        "subject": "시험 성적이 공개되었습니다",
        "body": "시험 성적이 공개되었습니다.\n상세 결과를 확인해 주세요.",
        "minutes_before": None,
    },
    "retake_assigned": {
        "category": "exam",
        "name": "[{academy_name}] 재시험 대상 안내",
        "subject": "재시험 대상으로 지정되었습니다",
        "body": "재시험 대상으로 지정되었습니다.\n재시험 일정과 범위를 확인해 주세요.",
        "minutes_before": None,
    },
    # ITEM_LIST(score) 템플릿 — 과제
    "assignment_registered": {
        "category": "assignment",
        "name": "[{academy_name}] 새 과제 등록 안내",
        "subject": "새로운 과제가 등록되었습니다",
        "body": "새로운 과제가 등록되었습니다.\n과제 내용과 제출 기한을 확인해 주세요.",
        "minutes_before": None,
    },
    "assignment_due_hours_before": {
        "category": "assignment",
        "name": "[{academy_name}] 과제 마감 임박 알림",
        "subject": "과제 제출 마감이 다가오고 있습니다",
        "body": "과제 제출 마감이 얼마 남지 않았습니다.\n아직 제출하지 않았다면 서둘러 주세요.",
        "minutes_before": 180,
    },
    "assignment_not_submitted": {
        "category": "assignment",
        "name": "[{academy_name}] 과제 미제출 알림",
        "subject": "과제가 미제출 상태입니다",
        "body": "과제가 아직 미제출 상태입니다.\n가능한 빨리 과제를 제출해 주세요.",
        "minutes_before": None,
    },
    # ITEM_LIST(score) 템플릿 — 성적
    "monthly_report_generated": {
        "category": "grades",
        "name": "[{academy_name}] 월간 성적 리포트",
        "subject": "이번 달 성적 리포트가 생성되었습니다",
        "body": "이번 달 성적 리포트가 생성되었습니다.\n시험·과제·출결 종합 분석 결과를 확인하세요.",
        "minutes_before": None,
    },
    # ───────── 클리닉/상담 (통합 알림톡: clinic_info / clinic_change 리스트형 템플릿) ─────────
    # body = #{선생님메모} 변수에만 들어가는 안내 문구.
    # 학원이름, 학생이름, 장소, 날짜, 시간, 사이트링크는 솔라피 리스트형 템플릿에 하드코딩.
    # ITEM_LIST 템플릿이 header(학원이름), highlight(학생이름), item.list(장소/날짜/시간)를
    # 자동 렌더링하므로, body(선생님메모)에는 안내 문구만 넣는다.
    # 장소/날짜/시간을 body에 중복으로 넣으면 카카오톡에서 이중 표시됨.
    "clinic_reminder": {
        "category": "clinic",
        "name": "[{academy_name}] 클리닉 시작 알림",
        "subject": "클리닉이 곧 시작됩니다",
        "body": "클리닉이 곧 시작됩니다.\n시간에 맞춰 준비해 주세요.",
        "minutes_before": 30,
    },
    "clinic_reservation_created": {
        "category": "clinic",
        "name": "[{academy_name}] 클리닉 예약 완료",
        "subject": "클리닉 예약이 완료되었습니다",
        "body": "클리닉 예약이 완료되었습니다.\n변경이 필요하시면 학원으로 연락 주세요.",
        "minutes_before": None,
    },
    "clinic_reservation_changed": {
        "category": "clinic",
        "name": "[{academy_name}] 클리닉 예약 변경 안내",
        "subject": "클리닉 예약이 변경되었습니다",
        "body": "클리닉 일정이 변경되었습니다.\n변경된 일정을 확인해 주세요.",
        "minutes_before": None,
    },
    "clinic_self_study_completed": {
        "category": "clinic",
        "name": "[{academy_name}] 클리닉 완료 안내",
        "subject": "클리닉이 완료되었습니다",
        "body": "클리닉이 완료되었습니다.\n수고하셨습니다.",
        "minutes_before": None,
    },
    "clinic_cancelled": {
        "category": "clinic",
        "name": "[{academy_name}] 클리닉 예약 취소 안내",
        "subject": "클리닉 예약이 취소되었습니다",
        "body": "클리닉 예약이 취소되었습니다.\n재예약이 필요하시면 학원으로 연락 주세요.",
        "minutes_before": None,
    },
    "clinic_check_in": {
        "category": "clinic",
        "name": "[{academy_name}] 클리닉 입실 알림",
        "subject": "클리닉에 입실하였습니다",
        "body": "클리닉에 입실하였습니다.",
        "minutes_before": None,
    },
    "clinic_check_out": {
        "category": "clinic",
        "name": "[{academy_name}] 클리닉 퇴실 알림",
        "subject": "클리닉에서 퇴실하였습니다",
        "body": "클리닉에서 퇴실하였습니다.\n수고하셨습니다.",
        "minutes_before": None,
    },
    "clinic_absent": {
        "category": "clinic",
        "name": "[{academy_name}] 클리닉 결석 알림",
        "subject": "클리닉에 결석하였습니다",
        "body": "예정된 클리닉에 결석하였습니다.\n사유가 있으시면 학원으로 연락 부탁드립니다.",
        "minutes_before": None,
    },
    "clinic_result_notification": {
        "category": "clinic",
        "name": "[{academy_name}] 클리닉 결과 안내",
        "subject": "클리닉 결과를 안내드립니다",
        "body": "클리닉 결과를 안내드립니다.\n상세 내용은 학원에 문의해 주세요.",
        "minutes_before": None,
    },
    "counseling_reservation_created": {
        "category": "clinic",
        "name": "[{academy_name}] 상담 예약 완료",
        "subject": "상담 예약이 완료되었습니다",
        "body": "상담 예약이 완료되었습니다.\n시간에 맞춰 방문해 주세요.",
        "minutes_before": None,
    },
    # ───────── 결제 ─────────
    # ITEM_LIST(score) 템플릿 — 결제
    "payment_complete": {
        "category": "payment",
        "name": "[{academy_name}] 결제 완료 안내",
        "subject": "결제가 완료되었습니다",
        "body": "결제가 정상적으로 완료되었습니다.\n감사합니다.",
        "minutes_before": None,
    },
    "payment_due_days_before": {
        "category": "payment",
        "name": "[{academy_name}] 납부 예정일 안내",
        "subject": "납부 예정일이 다가오고 있습니다",
        "body": "수강료 납부 예정일이 다가오고 있습니다.\n납부 기한 내 결제를 부탁드립니다.",
        "minutes_before": 4320,
    },
    # ───────── 자유양식 (카카오 공지형 3-1 구조) ─────────
    # 고정 문구(인사말·구분선·안내문) + #{공지내용} 변수 1개.
    # 카카오 검수 통과를 위해 고정 구조를 반드시 유지하고, #{공지내용}에만 자유 입력.
    # 변수 예시 텍스트는 검수 신청 시 별도 제출 (solapi_template_client 참조).
    "freeform_general": {
        "category": "notice",
        "name": "[{academy_name}] 공지사항 안내",
        "subject": "",
        "body": (
            "[{academy_name}] 공지사항 안내\n"
            "\n"
            "#{학생이름2}님, 안녕하세요.\n"
            "\n"
            "아래 내용을 안내드립니다.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "#{공지내용}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "확인 후 문의사항은 학원으로 연락 주세요."
        ),
        "minutes_before": None,
    },
    "freeform_grades": {
        "category": "grades",
        "name": "[{academy_name}] 성적 안내",
        "subject": "",
        "body": (
            "[{academy_name}] 성적 안내\n"
            "\n"
            "#{학생이름2}님, 안녕하세요.\n"
            "\n"
            "아래 내용을 안내드립니다.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "#{공지내용}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "확인 후 문의사항은 학원으로 연락 주세요."
        ),
        "minutes_before": None,
    },
    "freeform_lecture": {
        "category": "attendance",
        "name": "[{academy_name}] 수업 안내",
        "subject": "",
        "body": (
            "[{academy_name}] 수업 안내\n"
            "\n"
            "#{학생이름2}님, 안녕하세요.\n"
            "\n"
            "아래 내용을 안내드립니다.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "#{공지내용}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "확인 후 문의사항은 학원으로 연락 주세요."
        ),
        "minutes_before": None,
    },
    "freeform_exam": {
        "category": "exam",
        "name": "[{academy_name}] 시험 안내",
        "subject": "",
        "body": (
            "[{academy_name}] 시험 안내\n"
            "\n"
            "#{학생이름2}님, 안녕하세요.\n"
            "\n"
            "아래 내용을 안내드립니다.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "#{공지내용}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "확인 후 문의사항은 학원으로 연락 주세요."
        ),
        "minutes_before": None,
    },
    "freeform_assignment": {
        "category": "assignment",
        "name": "[{academy_name}] 과제 안내",
        "subject": "",
        "body": (
            "[{academy_name}] 과제 안내\n"
            "\n"
            "#{학생이름2}님, 안녕하세요.\n"
            "\n"
            "아래 내용을 안내드립니다.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "#{공지내용}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "확인 후 문의사항은 학원으로 연락 주세요."
        ),
        "minutes_before": None,
    },
    "freeform_payment": {
        "category": "payment",
        "name": "[{academy_name}] 수납 안내",
        "subject": "",
        "body": (
            "[{academy_name}] 수납 안내\n"
            "\n"
            "#{학생이름2}님, 안녕하세요.\n"
            "\n"
            "아래 내용을 안내드립니다.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "#{공지내용}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "확인 후 문의사항은 학원으로 연락 주세요."
        ),
        "minutes_before": None,
    },
    "freeform_clinic": {
        "category": "clinic",
        "name": "[{academy_name}] 보충수업 안내",
        "subject": "",
        "body": (
            "[{academy_name}] 보충수업 안내\n"
            "\n"
            "#{학생이름2}님, 안녕하세요.\n"
            "\n"
            "아래 내용을 안내드립니다.\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "#{공지내용}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "확인 후 문의사항은 학원으로 연락 주세요."
        ),
        "minutes_before": None,
    },
    # ───────── 영상 ─────────
    "video_encoding_complete": {
        "category": "lecture",
        "name": "[{academy_name}] 영상 인코딩 완료",
        "subject": "영상 인코딩이 완료되었습니다",
        "body": "영상 인코딩이 완료되었습니다.\n앱에서 영상을 확인해 주세요.",
        "minutes_before": None,
    },
    # ───────── 커뮤니티 (통합 알림톡: score 템플릿) ─────────
    # body = #{선생님메모} 영역. 학원이름/학생이름/제목/카테고리는 ITEM_LIST 슬롯 사용.
    "qna_answered": {
        "category": "community",
        "name": "[{academy_name}] 질문 답변 완료",
        "subject": "질문에 답변이 등록되었습니다",
        "body": "선생님이 질문에 답변하셨습니다.\n앱에서 답변 내용을 확인해 주세요.",
        "minutes_before": None,
    },
    "counsel_answered": {
        "category": "community",
        "name": "[{academy_name}] 상담 답변 등록",
        "subject": "상담 답변이 등록되었습니다",
        "body": "신청하신 상담에 답변이 등록되었습니다.\n앱에서 상세 내용을 확인해 주세요.",
        "minutes_before": None,
    },
    # ───────── 운영공지 ─────────
    # urgent_notice: 카카오 알림톡 정책 위반으로 제거
}


def get_default_templates(academy_name: str) -> dict[str, dict]:
    """_TEMPLATE_DEFINITIONS의 {academy_name} 플레이스홀더를 실제 학원명으로 치환하여 반환."""
    result = copy.deepcopy(_TEMPLATE_DEFINITIONS)
    for _trigger, tpl in result.items():
        for field in ("name", "subject", "body"):
            if field in tpl and isinstance(tpl[field], str):
                tpl[field] = tpl[field].replace("{academy_name}", academy_name)
    return result


# 하위 호환: academy_name 없이 import 하는 코드용 (플레이스홀더 그대로 유지)
DEFAULT_TEMPLATES = _TEMPLATE_DEFINITIONS
