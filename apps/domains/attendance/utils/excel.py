# apps/domains/attendance/utils/excel.py
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.domains.lectures.models import Session
from apps.domains.enrollment.models import Enrollment
from apps.domains.attendance.models import Attendance


STATUS_LABEL_MAP = {
    "PRESENT": "현장",
    "LATE": "지각",
    "ONLINE": "영상",
    "SUPPLEMENT": "보강",
    "EARLY_LEAVE": "조퇴",
    "ABSENT": "결석",
    "RUNAWAY": "출튀",
    "MATERIAL": "자료",
    "INACTIVE": "부재",
    "SECESSION": "퇴원",
}

STATUS_FILL_MAP = {
    "PRESENT": "C6EFCE",
    "ABSENT": "FFC7CE",
    "LATE": "FFEB9C",
    "ONLINE": "BDD7EE",
}


def build_attendance_excel(lecture):
    sessions = Session.objects.filter(
        lecture=lecture
    ).order_by("order", "id")

    enrollments = Enrollment.objects.filter(
        lecture=lecture,
        status="ACTIVE",
    ).select_related("student").order_by("student__name", "id")

    attendances = Attendance.objects.filter(
        session__lecture=lecture,
        enrollment__in=enrollments,
    )

    attendance_map = {
        (a.enrollment_id, a.session_id): a
        for a in attendances
    }

    wb = Workbook()
    ws = wb.active
    ws.title = "출결"

    # Header
    header = ["학생명", "학생번호", "학부모번호"]
    for s in sessions:
        label = f"{s.order}차시"
        if s.date:
            label += f" ({s.date})"
        header.append(label)

    ws.append(header)

    header_font = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for col in range(1, len(header) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.alignment = center
        ws.column_dimensions[get_column_letter(col)].width = 16

    ws.freeze_panes = "D2"

    for en in enrollments:
        row = [
            en.student.name,
            en.student.phone or "",
            en.student.parent_phone or "",
        ]

        for s in sessions:
            att = attendance_map.get((en.id, s.id))
            code = att.status if att else ""
            label = STATUS_LABEL_MAP.get(code, code)
            row.append(label)

        ws.append(row)
        r = ws.max_row

        for idx, s in enumerate(sessions, start=4):
            att = attendance_map.get((en.id, s.id))
            if att and att.status in STATUS_FILL_MAP:
                ws.cell(row=r, column=idx).fill = PatternFill(
                    start_color=STATUS_FILL_MAP[att.status],
                    end_color=STATUS_FILL_MAP[att.status],
                    fill_type="solid",
                )
            ws.cell(row=r, column=idx).alignment = center

    filename = f"출결_{lecture.title}_{lecture.id}.xlsx"
    return wb, filename
