"""
트리거별 기본 템플릿 정의.
테넌트가 처음 자동발송 설정에 접근할 때 자동 생성됨.
"""

# trigger -> { category, name, subject, body, minutes_before }
DEFAULT_TEMPLATES: dict[str, dict] = {
    # ───────── 가입/등록 ─────────
    "student_signup": {
        "category": "signup",
        "name": "[학원플러스] 가입 완료 안내",
        "subject": "학원플러스 가입을 환영합니다",
        "body": (
            "#{student_name_2}학생님, 학원플러스 가입이 완료되었습니다.\n"
            "\n"
            "아래 링크에서 학원 앱에 접속하실 수 있습니다.\n"
            "#{site_link}\n"
            "\n"
            "수업 일정, 과제, 시험 등 모든 학원 정보를 앱에서 확인하세요.\n"
            "궁금하신 점은 학원으로 문의해 주세요."
        ),
        "minutes_before": None,
    },
    "registration_approved_student": {
        "category": "signup",
        "name": "[학원플러스] 가입 승인 안내 (학생)",
        "subject": "가입 신청이 승인되었습니다",
        "body": (
            "#{student_name}학생님, 가입 신청이 승인되었습니다.\n"
            "\n"
            "▶ 로그인 정보\n"
            "아이디: #{student_id}\n"
            "비밀번호: #{student_password}\n"
            "\n"
            "▶ 접속 링크\n"
            "#{site_link}\n"
            "\n"
            "#{pw_notice}\n"
            "첫 로그인 후 비밀번호를 반드시 변경해 주세요."
        ),
        "minutes_before": None,
    },
    "registration_approved_parent": {
        "category": "signup",
        "name": "[학원플러스] 가입 승인 안내 (학부모)",
        "subject": "학부모 계정이 승인되었습니다",
        "body": (
            "#{student_name}학생 학부모님, 안녕하세요.\n"
            "가입 신청이 승인되었습니다.\n"
            "\n"
            "▶ 학부모 로그인 정보\n"
            "아이디: #{parent_id}\n"
            "비밀번호: #{parent_password}\n"
            "\n"
            "▶ 학생 로그인 정보\n"
            "아이디: #{student_id}\n"
            "비밀번호: #{student_password}\n"
            "\n"
            "▶ 접속 링크\n"
            "#{site_link}\n"
            "\n"
            "#{pw_notice}\n"
            "자녀의 수업·성적·출결을 앱에서 확인하실 수 있습니다."
        ),
        "minutes_before": None,
    },
    "withdrawal_complete": {
        "category": "signup",
        "name": "[학원플러스] 퇴원 처리 완료",
        "subject": "퇴원 처리가 완료되었습니다",
        "body": (
            "#{student_name_2}학생님, 퇴원 처리가 완료되었습니다.\n"
            "\n"
            "그동안 학원을 이용해 주셔서 감사합니다.\n"
            "추후 재등록을 원하시면 언제든 문의해 주세요.\n"
            "\n"
            "학생의 앞날을 응원합니다."
        ),
        "minutes_before": None,
    },
    # ───────── 출결 ─────────
    "lecture_session_reminder": {
        "category": "attendance",
        "name": "[학원플러스] 수업 시작 알림",
        "subject": "오늘 수업이 곧 시작됩니다",
        "body": (
            "#{student_name_2}학생님, 오늘 수업이 곧 시작됩니다.\n"
            "\n"
            "▶ 수업 정보\n"
            "강의: #{lecture_name}\n"
            "차시: #{session_name}\n"
            "시간: #{date} #{time}\n"
            "\n"
            "준비물을 챙기고 시간에 맞춰 입실해 주세요.\n"
            "#{site_link}"
        ),
        "minutes_before": 30,
    },
    "check_in_complete": {
        "category": "attendance",
        "name": "[학원플러스] 입실 완료 알림",
        "subject": "학생이 입실하였습니다",
        "body": (
            "#{student_name_2}학생이 학원에 입실하였습니다.\n"
            "\n"
            "강의: #{lecture_name}\n"
            "차시: #{session_name}\n"
            "시간: #{date} #{time}\n"
            "\n"
            "출결 현황은 앱에서 확인하실 수 있습니다.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    "absent_occurred": {
        "category": "attendance",
        "name": "[학원플러스] 결석 발생 알림",
        "subject": "결석이 발생하였습니다",
        "body": (
            "#{student_name_2}학생님의 수업에 결석이 발생하였습니다.\n"
            "\n"
            "강의: #{lecture_name}\n"
            "차시: #{session_name}\n"
            "날짜: #{date}\n"
            "\n"
            "사유가 있으시면 학원으로 연락 부탁드립니다.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    # ───────── 시험 ─────────
    "exam_scheduled_days_before": {
        "category": "exam",
        "name": "[학원플러스] 시험 예정 안내",
        "subject": "시험이 예정되어 있습니다",
        "body": (
            "#{student_name_2}학생님, 시험이 예정되어 있습니다.\n"
            "\n"
            "▶ 시험 정보\n"
            "시험명: #{exam_name}\n"
            "강의: #{lecture_name}\n"
            "일시: #{date} #{time}\n"
            "\n"
            "시험 범위를 앱에서 확인하고 미리 준비해 주세요.\n"
            "#{site_link}"
        ),
        "minutes_before": 1440,
    },
    "exam_start_minutes_before": {
        "category": "exam",
        "name": "[학원플러스] 시험 시작 알림",
        "subject": "시험이 곧 시작됩니다",
        "body": (
            "#{student_name_2}학생님, 시험이 곧 시작됩니다.\n"
            "\n"
            "시험명: #{exam_name}\n"
            "강의: #{lecture_name}\n"
            "시간: #{time}\n"
            "\n"
            "앱에서 시험에 응시할 수 있습니다.\n"
            "#{site_link}"
        ),
        "minutes_before": 30,
    },
    "exam_not_taken": {
        "category": "exam",
        "name": "[학원플러스] 시험 미응시 알림",
        "subject": "시험에 응시하지 않았습니다",
        "body": (
            "#{student_name_2}학생님, 예정된 시험에 아직 응시하지 않았습니다.\n"
            "\n"
            "시험명: #{exam_name}\n"
            "강의: #{lecture_name}\n"
            "\n"
            "응시 기한 내에 반드시 시험을 완료해 주세요.\n"
            "미응시 시 불합격 처리될 수 있습니다.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    "exam_score_published": {
        "category": "grades",
        "name": "[학원플러스] 성적 공개 안내",
        "subject": "시험 성적이 공개되었습니다",
        "body": (
            "#{student_name_2}학생님, 시험 성적이 공개되었습니다.\n"
            "\n"
            "시험명: #{exam_name}\n"
            "강의: #{lecture_name}\n"
            "성적: #{exam_score}\n"
            "\n"
            "앱에서 상세 결과를 확인하실 수 있습니다.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    "retake_assigned": {
        "category": "exam",
        "name": "[학원플러스] 재시험 대상 안내",
        "subject": "재시험 대상으로 지정되었습니다",
        "body": (
            "#{student_name_2}학생님, 재시험 대상으로 지정되었습니다.\n"
            "\n"
            "시험명: #{exam_name}\n"
            "강의: #{lecture_name}\n"
            "\n"
            "재시험 일정과 범위를 앱에서 확인해 주세요.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    # ───────── 과제 ─────────
    "assignment_registered": {
        "category": "assignment",
        "name": "[학원플러스] 새 과제 등록 안내",
        "subject": "새로운 과제가 등록되었습니다",
        "body": (
            "#{student_name_2}학생님, 새로운 과제가 등록되었습니다.\n"
            "\n"
            "▶ 과제 정보\n"
            "과제명: #{assignment_name}\n"
            "강의: #{lecture_name}\n"
            "차시: #{session_name}\n"
            "\n"
            "과제 내용과 제출 기한을 앱에서 확인해 주세요.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    "assignment_due_hours_before": {
        "category": "assignment",
        "name": "[학원플러스] 과제 마감 임박 알림",
        "subject": "과제 제출 마감이 다가오고 있습니다",
        "body": (
            "#{student_name_2}학생님, 과제 제출 마감이 얼마 남지 않았습니다.\n"
            "\n"
            "과제명: #{assignment_name}\n"
            "강의: #{lecture_name}\n"
            "\n"
            "아직 제출하지 않았다면 서둘러 주세요.\n"
            "마감 후에는 제출이 어려울 수 있습니다.\n"
            "#{site_link}"
        ),
        "minutes_before": 180,
    },
    "assignment_not_submitted": {
        "category": "assignment",
        "name": "[학원플러스] 과제 미제출 알림",
        "subject": "과제가 미제출 상태입니다",
        "body": (
            "#{student_name_2}학생님, 과제가 아직 미제출 상태입니다.\n"
            "\n"
            "과제명: #{assignment_name}\n"
            "강의: #{lecture_name}\n"
            "\n"
            "가능한 빨리 과제를 제출해 주세요.\n"
            "사유가 있으시면 담당 선생님께 말씀해 주세요.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    # ───────── 성적 ─────────
    "monthly_report_generated": {
        "category": "grades",
        "name": "[학원플러스] 월간 성적 리포트",
        "subject": "이번 달 성적 리포트가 생성되었습니다",
        "body": (
            "#{student_name_2}학생님, 이번 달 성적 리포트가 생성되었습니다.\n"
            "\n"
            "시험·과제·출결 종합 분석 결과를 앱에서 확인하세요.\n"
            "#{site_link}\n"
            "\n"
            "꾸준한 성장을 응원합니다."
        ),
        "minutes_before": None,
    },
    # ───────── 클리닉/상담 ─────────
    "clinic_reminder": {
        "category": "clinic",
        "name": "[학원플러스] 클리닉 시작 알림",
        "subject": "클리닉이 곧 시작됩니다",
        "body": (
            "#{student_name_2}학생님, 클리닉이 곧 시작됩니다.\n"
            "\n"
            "클리닉: #{clinic_name}\n"
            "장소: #{clinic_place}\n"
            "시간: #{date} #{time}\n"
            "\n"
            "시간에 맞춰 준비해 주세요.\n"
            "#{site_link}"
        ),
        "minutes_before": 30,
    },
    "clinic_reservation_created": {
        "category": "clinic",
        "name": "[학원플러스] 클리닉 예약 완료",
        "subject": "클리닉 예약이 완료되었습니다",
        "body": (
            "#{student_name_2}학생님, 클리닉 예약이 완료되었습니다.\n"
            "\n"
            "클리닉: #{clinic_name}\n"
            "장소: #{clinic_place}\n"
            "일시: #{date} #{time}\n"
            "\n"
            "변경이 필요하시면 앱에서 수정해 주세요.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    "clinic_reservation_changed": {
        "category": "clinic",
        "name": "[학원플러스] 클리닉 예약 변경 안내",
        "subject": "클리닉 예약이 변경되었습니다",
        "body": (
            "#{student_name_2}학생님, 클리닉 예약 일정이 변경되었습니다.\n"
            "\n"
            "클리닉: #{clinic_name}\n"
            "장소: #{clinic_place}\n"
            "변경 일시: #{date} #{time}\n"
            "\n"
            "변경된 일정을 확인해 주세요.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    "counseling_reservation_created": {
        "category": "clinic",
        "name": "[학원플러스] 상담 예약 완료",
        "subject": "상담 예약이 완료되었습니다",
        "body": (
            "#{student_name_2}학생님, 상담 예약이 완료되었습니다.\n"
            "\n"
            "장소: #{clinic_place}\n"
            "일시: #{date} #{time}\n"
            "\n"
            "시간에 맞춰 방문해 주세요.\n"
            "#{site_link}"
        ),
        "minutes_before": None,
    },
    # ───────── 결제 ─────────
    "payment_complete": {
        "category": "payment",
        "name": "[학원플러스] 결제 완료 안내",
        "subject": "결제가 완료되었습니다",
        "body": (
            "#{student_name_2}학생님, 결제가 정상적으로 완료되었습니다.\n"
            "\n"
            "결제 내역은 앱에서 확인하실 수 있습니다.\n"
            "#{site_link}\n"
            "\n"
            "감사합니다."
        ),
        "minutes_before": None,
    },
    "payment_due_days_before": {
        "category": "payment",
        "name": "[학원플러스] 납부 예정일 안내",
        "subject": "납부 예정일이 다가오고 있습니다",
        "body": (
            "#{student_name_2}학생님, 수강료 납부 예정일이 다가오고 있습니다.\n"
            "\n"
            "납부 기한 내 결제를 부탁드립니다.\n"
            "#{site_link}\n"
            "\n"
            "이미 납부하셨다면 이 메시지를 무시해 주세요."
        ),
        "minutes_before": 4320,
    },
    # ───────── 운영공지 ─────────
    "urgent_notice": {
        "category": "notice",
        "name": "[학원플러스] 긴급 공지",
        "subject": "긴급 공지사항",
        "body": (
            "#{student_name_2}학생님, 학원에서 긴급 공지사항을 안내드립니다.\n"
            "\n"
            "상세 내용은 앱에서 확인해 주세요.\n"
            "#{site_link}\n"
            "\n"
            "중요한 내용이니 반드시 확인 부탁드립니다."
        ),
        "minutes_before": None,
    },
}
