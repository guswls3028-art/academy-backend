"""
알림톡 템플릿 변수 — 비즈니스 채널 승인 전에도 미리 확정·등록용.

솔라피/카카오 콘솔에 등록할 템플릿 문구의 변수명을 코드에 고정해 두고,
DB 필드와 매칭해 치환 데이터를 만듭니다.
"""

from __future__ import annotations

from typing import Any, Dict

# -----------------------------------------------------------------------------
# 템플릿 변수명 (카카오 #{변수명} 형식, 솔라피 콘솔에 등록 시 동일하게)
# -----------------------------------------------------------------------------

# 클리닉/예약 관련 (예: 예약 안내, 리마인더)
ALIMTALK_VAR_NAME = "name"           # 수신자 이름 (학생/학부모)
ALIMTALK_VAR_DATE = "date"           # 예약일 또는 안내 날짜 (예: 2025-02-15)
ALIMTALK_VAR_TIME = "time"           # 시간 (예: 14:00)
ALIMTALK_VAR_CLINIC_NAME = "clinic_name"  # 클리닉/상담명
ALIMTALK_VAR_PLACE = "place"         # 장소
ALIMTALK_VAR_LINK = "link"           # 상세 링크 (선택)

# 공통
ALIMTALK_VAR_TITLE = "title"         # 제목/안내 제목

# 템플릿 문구 예시 (승인 받을 때 사용할 문구에 들어갈 변수)
# 예: "#{name}님, #{date} #{time} #{clinic_name} 예약이 완료되었습니다."
ALIMTALK_TEMPLATE_VARIABLES = [
    ALIMTALK_VAR_NAME,
    ALIMTALK_VAR_DATE,
    ALIMTALK_VAR_TIME,
    ALIMTALK_VAR_CLINIC_NAME,
    ALIMTALK_VAR_PLACE,
    ALIMTALK_VAR_LINK,
    ALIMTALK_VAR_TITLE,
]


def build_replacements(context: Dict[str, Any]) -> list[dict]:
    """
    DB/컨텍스트 dict를 Solapi 알림톡 replacements 형식으로 변환.

    Solapi RequestMessage.replacements 형식:
    [ {"key": "name", "value": "홍길동"}, ... ]

    context 예시 (클리닉 예약 리마인더):
        {"name": "홍길동", "date": "2025-02-15", "time": "14:00", "clinic_name": "수학 클리닉", "place": "A관 301"}
    """
    key_to_var = {
        "name": ALIMTALK_VAR_NAME,
        "date": ALIMTALK_VAR_DATE,
        "time": ALIMTALK_VAR_TIME,
        "clinic_name": ALIMTALK_VAR_CLINIC_NAME,
        "place": ALIMTALK_VAR_PLACE,
        "link": ALIMTALK_VAR_LINK,
        "title": ALIMTALK_VAR_TITLE,
    }
    out = []
    for ctx_key, var_name in key_to_var.items():
        if ctx_key in context and context[ctx_key] is not None:
            out.append({"key": var_name, "value": str(context[ctx_key])})
    return out


def template_context_from_reservation(reservation: Any) -> Dict[str, Any]:
    """
    예약 모델(또는 dict)에서 알림톡 context 추출.
    프로젝트에 Reservation 모델이 생기면 여기서 필드 매핑.

    예시 (추후 구현):
        return {
            "name": reservation.student.name if hasattr(reservation, "student") else "",
            "date": reservation.date.strftime("%Y-%m-%d") if getattr(reservation, "date", None) else "",
            "time": getattr(reservation, "time", "") or "",
            "clinic_name": getattr(reservation, "clinic_name", "") or getattr(reservation, "title", ""),
            "place": getattr(reservation, "place", "") or "",
        }
    """
    if hasattr(reservation, "__dict__"):
        d = reservation.__dict__ if not hasattr(reservation, "pk") else {}
        return {
            "name": d.get("student_name") or getattr(reservation, "student_name", "") or "",
            "date": d.get("date") or getattr(reservation, "date", ""),
            "time": d.get("time") or getattr(reservation, "time", "") or "",
            "clinic_name": d.get("clinic_name") or getattr(reservation, "clinic_name", "") or getattr(reservation, "title", "") or "",
            "place": d.get("place") or getattr(reservation, "place", "") or "",
        }
    if isinstance(reservation, dict):
        return {
            "name": reservation.get("name", ""),
            "date": reservation.get("date", ""),
            "time": reservation.get("time", ""),
            "clinic_name": reservation.get("clinic_name", "") or reservation.get("title", ""),
            "place": reservation.get("place", ""),
            "link": reservation.get("link", ""),
            "title": reservation.get("title", ""),
        }
    return {}
