"""Roster-based practice inspection workbook. Only explicit nonblank cells are imported."""
from collections import Counter, defaultdict
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from apps.shared.utils.excel_workbook import validate_xlsx_archive


HEADERS = ("구분", "이름", "풀어 온 문제 수", "총 문제 수", "코멘트", "수강번호", "버전")
MAX_ROWS = 5000


def build_template(scope, title, items, total):
    book = Workbook()
    sheet = book.active
    sheet.title = "과제 검사"
    sheet.append([f"{title} · 과제 검사 일괄 입력"])
    sheet.append(["작성 안내"])
    sheet.append(["풀어 온 문제 수와 총 문제 수를 함께 적으세요. 코멘트만 입력해도 됩니다."])
    sheet.append(["빈칸은 기존 값 유지, 숫자 0은 실제 풀이 0개입니다. 예시 행은 반영하지 않습니다."])
    sheet.append(["학생과 학부모에게 공개됩니다. 동명이인은 수강번호로 구분하세요."])
    sheet.append(list(HEADERS))
    sheet.append(["예시", "예시 학생", 12, 15, "건너뛴 예제를 다음 시간에 확인해요."])
    for row_number, row in enumerate(items, start=sheet.max_row + 1):
        sheet.append([None, row["student_name"], None, total, None, row["enrollment_id"], row["version"]])
        sheet.cell(row_number, 2).data_type = "s"
    sheet["A1"].data_type = "s"
    for n in range(1, 6):
        sheet.merge_cells(start_row=n, start_column=1, end_row=n, end_column=7)
    sheet["A1"].font = Font(bold=True, size=15)
    for cell in sheet[6]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="334D72")
        cell.alignment = Alignment(vertical="center")
    for col, width in zip("ABCDEFG", (12, 20, 20, 16, 64, 14, 10)):
        sheet.column_dimensions[col].width = width
    sheet.column_dimensions["G"].hidden = True
    sheet.freeze_panes = "C7"
    meta = book.create_sheet("양식 정보")
    meta.append(list(scope))
    meta.sheet_state = "hidden"
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def read_preview(upload, scope, items):
    validate_xlsx_archive(upload)
    book = load_workbook(upload, read_only=True, data_only=False, keep_links=False)
    try:
        if "양식 정보" in book.sheetnames and tuple(next(book["양식 정보"].values, ())) != tuple(scope):
            raise ValueError("다른 학원 또는 과제의 양식입니다. 현재 과제의 양식을 사용해 주세요.")
        sheet = book["과제 검사"] if "과제 검사" in book.sheetnames else book.active
        if not sheet or (sheet.max_row or 0) > MAX_ROWS + 35 or (sheet.max_column or 0) > 50:
            raise ValueError("한 파일에는 최대 5,000명의 과제 결과를 입력할 수 있습니다.")
        return _read_sheet(sheet, items)
    finally:
        book.close()


def _read_sheet(sheet, items):
    by_id = {row["enrollment_id"]: row for row in items}
    by_name = defaultdict(list)
    for item in items:
        by_name[item["student_name"].strip()].append(item)
    columns = {}
    changes, errors = [], []
    rows = sheet.iter_rows(max_row=min(sheet.max_row or MAX_ROWS + 35, MAX_ROWS + 35), max_col=min(sheet.max_column or 50, 50))
    for line, cells in enumerate(rows, 1):
        values = [cell.value for cell in cells]
        if not columns:
            headers = [str(val).strip() if val is not None else "" for val in values]
            if all(key in headers for key in HEADERS[1:5]):
                if any(headers.count(key) > 1 for key in HEADERS if key in headers):
                    raise ValueError("같은 제목의 열이 두 개 이상 있습니다.")
                columns = {key: headers.index(key) for key in HEADERS if key in headers}
            elif line >= 30:
                break
            continue
        row = {key: values[i] for key, i in columns.items()}
        if str(row.get("구분", "")).strip() == "예시":
            continue
        if row["풀어 온 문제 수"] is None and row["코멘트"] in (None, ""):
            continue
        try:
            if any(getattr(cells[i], "data_type", "") == "f" for i in columns.values()):
                raise ValueError("수식 대신 값으로 입력해 주세요.")
            name = str(row["이름"] or "").strip()
            if row.get("수강번호") is not None:
                target = by_id.get(_integer(row["수강번호"], "수강번호"))
                if target is None or target["student_name"].strip() != name:
                    raise ValueError("명단의 이름과 수강번호가 일치하지 않습니다.")
            else:
                matches = by_name[name]
                if len(matches) != 1:
                    raise ValueError("명단에서 한 명을 찾을 수 없습니다. 전용 양식의 이름·수강번호를 확인해 주세요.")
                target = matches[0]
            change = {"enrollment_id": target["enrollment_id"], "version": target["version"]}
            if row.get("버전") is not None:
                change["version"] = _integer(row["버전"], "버전")
            if row["풀어 온 문제 수"] is not None:
                solved = _integer(row["풀어 온 문제 수"], "풀어 온 문제 수")
                total = _integer(row["총 문제 수"], "총 문제 수")
                if not 0 <= solved <= total or not 1 <= total <= 100000:
                    raise ValueError("풀이 수는 0 이상 총 문제 수 이하, 총 문제 수는 1~100,000이어야 합니다.")
                change.update(solved=solved, total=total)
            feedback = "" if row["코멘트"] is None else str(row["코멘트"]).strip()
            if feedback:
                if len(feedback) > 2000:
                    raise ValueError("코멘트는 2,000자 이내로 입력해 주세요.")
                change["feedback"] = feedback
            different = any(target[key] != value for key, value in change.items() if key not in {"version", "enrollment_id"})
            if different and change["version"] != target["version"]:
                raise ValueError("양식 다운로드 후 다른 수정이 있었습니다. 새 양식으로 확인해 주세요.")
            changes.append({"line": line, "name": name, "before": target, "change": change,
                            "status": ("변경" if target["version"] else "새 입력") if different else "동일"})
        except ValueError as exc:
            errors.append(f"{line}행 · {exc}")
    if not columns:
        raise ValueError("이름, 풀어 온 문제 수, 총 문제 수, 코멘트 열을 찾을 수 없습니다. 전용 양식을 확인해 주세요.")
    duplicates = Counter(entry["change"]["enrollment_id"] for entry in changes)
    for entry in changes:
        if duplicates[entry["change"]["enrollment_id"]] > 1:
            errors.append(f"{entry['line']}행 · 같은 학생이 여러 번 입력됐습니다.")
    return {"rows": changes, "errors": errors}


def _integer(value, label):
    if isinstance(value, bool) or not str(value).strip().isascii() or not str(value).strip().isdecimal():
        raise ValueError(f"{label}에는 정수를 입력해 주세요.")
    return int(value)
