from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from apps.domains.results.guards.exam_enrollment_guard import (
    validate_exam_enrollment_assigned,
)
from apps.domains.results.models import ExamAttempt, Result, ResultFact, ResultItem
from apps.support.omr.score_adjustment import get_score_adjustment_from_answers
from apps.support.omr.score_shape import get_exam_score_shape
from apps.support.omr.sheet_resolver import resolve_omr_sheet_for_exam
from apps.support.results.admin_exam_dependencies import dispatch_progress_pipeline
from apps.support.results.exam_result_excel_import_dependencies import (
    ResultImportCandidateRecord,
    get_answer_key_answers,
    get_locked_enrollment_for_tenant,
    get_result_import_candidates,
    get_result_import_questions,
)


logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ROWS = 2_000
MAX_COLUMNS = 600
MAX_HEADER_SCAN_ROWS = 30

_NAME_HEADERS = {"이름", "학생명", "성명", "name", "studentname"}
_ENROLLMENT_HEADERS = {
    "수강등록id",
    "수강등록번호",
    "enrollmentid",
    "enrollment",
}
_STUDENT_PHONE_HEADERS = {
    "학생연락처",
    "학생전화번호",
    "학생핸드폰",
    "학생휴대폰",
    "studentphone",
}
_PARENT_PHONE_HEADERS = {
    "부모님연락처",
    "부모연락처",
    "학부모연락처",
    "학부모전화번호",
    "보호자연락처",
    "parentphone",
}
_CORRECT_MARKERS = {"o", "○", "◯", "정답", "맞음", "맞아요", "true", "1", "v", "✓"}
_WRONG_MARKERS = {"x", "×", "✕", "오답", "틀림", "틀렸음", "false", "0"}


class ExamResultWorkbookError(ValueError):
    pass


@dataclass(frozen=True)
class QuestionSpec:
    question_id: int
    number: int
    kind: str
    max_score: float


@dataclass(frozen=True)
class Candidate:
    enrollment_id: int
    student_name: str
    student_phone: str
    parent_phone: str
    school: str
    lecture_id: int | None
    lecture_title: str
    lecture_color: str
    lecture_chip_label: str

    @property
    def lectures_payload(self) -> list[dict[str, Any]]:
        if not self.lecture_title:
            return []
        return [
            {
                "id": self.lecture_id,
                "lecture_name": self.lecture_title,
                "color": self.lecture_color or None,
                "chip_label": self.lecture_chip_label or None,
            }
        ]


@dataclass(frozen=True)
class PlannedRow:
    source_row: int
    candidate: Candidate
    correctness: dict[int, bool]
    correct_count: int
    wrong_question_numbers: tuple[int, ...]
    total_score: float
    max_score: float
    will_overwrite: bool


@dataclass
class ImportPlan:
    exam: Any
    filename: str
    questions: list[QuestionSpec]
    rows: list[PlannedRow] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def can_apply(self) -> bool:
        return bool(self.rows) and not self.errors

    def as_payload(self, *, applied: bool = False) -> dict[str, Any]:
        overwrite_count = sum(1 for row in self.rows if row.will_overwrite)
        return {
            "ok": self.can_apply,
            "applied": bool(applied),
            "exam_id": int(self.exam.id),
            "exam_title": str(self.exam.title or ""),
            "filename": self.filename,
            "question_count": len(self.questions),
            "matched_count": len(self.rows),
            "overwrite_count": overwrite_count,
            "errors": self.errors,
            "warnings": self.warnings,
            "rows": [
                {
                    "row": row.source_row,
                    "enrollment_id": row.candidate.enrollment_id,
                    "student_name": row.candidate.student_name,
                    "lectures": row.candidate.lectures_payload,
                    "correct_count": row.correct_count,
                    "wrong_count": len(row.wrong_question_numbers),
                    "wrong_questions": list(row.wrong_question_numbers),
                    "total_score": row.total_score,
                    "max_score": row.max_score,
                    "will_overwrite": row.will_overwrite,
                }
                for row in self.rows
            ],
        }


def build_exam_result_template(*, exam: Any, tenant: Any) -> bytes:
    questions = _question_specs(exam=exam, tenant=tenant)
    candidates = _exam_candidates(exam=exam, tenant=tenant)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "시험결과"

    last_column = 6 + len(questions) + 1
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_column)
    sheet.cell(1, 1, _safe_excel_text(f"{exam.title} · 문항별 정오 입력"))
    sheet.cell(1, 1).font = Font(size=15, bold=True, color="FFFFFF")
    sheet.cell(1, 1).fill = PatternFill("solid", fgColor="1D4ED8")
    sheet.cell(1, 1).alignment = Alignment(horizontal="left", vertical="center")
    sheet.row_dimensions[1].height = 28

    guides = [
        "작성 방법: 틀린 문항만 X로 표시하세요. 정답은 빈칸 또는 O로 두면 됩니다.",
        "객관식·단답형이 섞여 있어도 문항 번호 기준으로 반영됩니다.",
        "수강등록ID와 학생 정보는 수정하지 마세요. 점수는 업로드 후 자동 계산됩니다.",
        "기존에 쓰던 엑셀도 이름(또는 연락처)과 1, 2, 3… 문항 열이 있으면 업로드할 수 있습니다.",
    ]
    for offset, text in enumerate(guides, start=3):
        sheet.merge_cells(
            start_row=offset,
            start_column=1,
            end_row=offset,
            end_column=last_column,
        )
        sheet.cell(offset, 1, text)
        sheet.cell(offset, 1).font = Font(size=10, color="475569")

    header_row = 8
    headers: list[Any] = [
        "수강등록ID",
        "학교",
        "이름",
        "학부모연락처",
        "학생연락처",
        "강의",
        *[question.number for question in questions],
        "점수(확인용)",
    ]
    for column, value in enumerate(headers, start=1):
        cell = sheet.cell(header_row, column, value)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    question_by_number = {question.number: question for question in questions}
    for column, question in enumerate(questions, start=7):
        cell = sheet.cell(header_row, column)
        cell.fill = PatternFill(
            "solid",
            fgColor="0F766E" if question.kind == "essay" else "2563EB",
        )
        cell.comment = None

    data_validation = DataValidation(
        type="list",
        formula1='"O,X"',
        allow_blank=True,
        error="정답은 빈칸 또는 O, 오답은 X로 입력해 주세요.",
        errorTitle="정오 표시 확인",
    )
    sheet.add_data_validation(data_validation)

    for row_index, candidate in enumerate(candidates, start=header_row + 1):
        values = [
            candidate.enrollment_id,
            _safe_excel_text(candidate.school),
            _safe_excel_text(candidate.student_name),
            _safe_excel_text(candidate.parent_phone),
            _safe_excel_text(candidate.student_phone),
            _safe_excel_text(candidate.lecture_title),
        ]
        for column, value in enumerate(values, start=1):
            sheet.cell(row_index, column, value)
        for question_column in range(7, 7 + len(questions)):
            sheet.cell(row_index, question_column, "")
        score_column = 7 + len(questions)
        score_terms = []
        for question_column, question_number in enumerate(
            [question.number for question in questions],
            start=7,
        ):
            question = question_by_number[question_number]
            letter = sheet.cell(row_index, question_column).column_letter
            score_terms.append(
                f'IF(OR(UPPER({letter}{row_index})="X",{letter}{row_index}="×"),0,{question.max_score})'
            )
        sheet.cell(row_index, score_column, f"=ROUND({'+'.join(score_terms) or '0'},1)")

    if candidates:
        first_question_column = 7
        last_question_column = 6 + len(questions)
        data_validation.add(
            f"{sheet.cell(header_row + 1, first_question_column).coordinate}:"
            f"{sheet.cell(header_row + len(candidates), last_question_column).coordinate}"
        )

    widths = [16, 16, 12, 18, 18, 18]
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for column in range(7, 7 + len(questions)):
        sheet.column_dimensions[get_column_letter(column)].width = 5
    sheet.column_dimensions[get_column_letter(7 + len(questions))].width = 13

    sheet.freeze_panes = f"G{header_row + 1}"
    sheet.auto_filter.ref = f"A{header_row}:{sheet.cell(header_row, last_column).coordinate}"
    sheet.sheet_view.showGridLines = False

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def plan_exam_result_import(
    *,
    exam: Any,
    tenant: Any,
    filename: str,
    workbook_bytes: bytes,
) -> ImportPlan:
    questions = _question_specs(exam=exam, tenant=tenant)
    plan = ImportPlan(exam=exam, filename=filename, questions=questions)
    candidates = _exam_candidates(exam=exam, tenant=tenant)
    if not candidates:
        plan.errors.append(_error(None, "students", "이 시험에 등록된 학생이 없습니다."))
        return plan

    try:
        worksheet = _load_first_worksheet(workbook_bytes)
        header_row_number, columns = _find_header(worksheet, questions)
    except ExamResultWorkbookError as exc:
        plan.errors.append(_error(None, "file", str(exc)))
        return plan

    expected_numbers = {question.number for question in questions}
    found_numbers = set(columns["questions"])
    missing = sorted(expected_numbers - found_numbers)
    extra = sorted(found_numbers - expected_numbers)
    if missing:
        plan.errors.append(
            _error(
                header_row_number,
                "questions",
                f"시험 문항 열이 빠져 있습니다: {', '.join(map(str, missing))}번",
            )
        )
    if extra:
        plan.errors.append(
            _error(
                header_row_number,
                "questions",
                f"이 시험에 없는 문항 열이 있습니다: {', '.join(map(str, extra))}번",
            )
        )
    if plan.errors:
        return plan

    by_id = {candidate.enrollment_id: candidate for candidate in candidates}
    by_name: dict[str, list[Candidate]] = {}
    by_phone: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_name.setdefault(_normalize_name(candidate.student_name), []).append(candidate)
        for phone in {candidate.student_phone, candidate.parent_phone}:
            normalized = _normalize_phone(phone)
            if normalized:
                by_phone.setdefault(normalized, []).append(candidate)

    existing_enrollment_ids = set(
        Result.objects.filter(
            target_type="exam",
            target_id=int(exam.id),
            enrollment_id__in=list(by_id),
        ).values_list("enrollment_id", flat=True)
    )
    question_by_number = {question.number: question for question in questions}
    used_enrollment_ids: set[int] = set()

    for row_number, values in enumerate(
        worksheet.iter_rows(
            min_row=header_row_number + 1,
            max_row=worksheet.max_row,
            max_col=worksheet.max_column,
            values_only=True,
        ),
        start=header_row_number + 1,
    ):
        row_values = tuple(values)
        identity_values = [
            _value_at(row_values, columns.get("enrollment")),
            _value_at(row_values, columns.get("name")),
            _value_at(row_values, columns.get("student_phone")),
            _value_at(row_values, columns.get("parent_phone")),
        ]
        question_values = [
            _value_at(row_values, column_index)
            for column_index in columns["questions"].values()
        ]
        if not any(_has_value(value) for value in identity_values + question_values):
            continue

        candidate, match_error = _match_candidate(
            row_values=row_values,
            columns=columns,
            by_id=by_id,
            by_name=by_name,
            by_phone=by_phone,
        )
        if match_error:
            plan.errors.append(_error(row_number, "student", match_error))
            continue
        assert candidate is not None
        if candidate.enrollment_id in used_enrollment_ids:
            plan.errors.append(
                _error(row_number, "student", "같은 학생이 엑셀에 두 번 들어 있습니다.")
            )
            continue

        correctness: dict[int, bool] = {}
        marker_error = False
        for question_number, column_index in columns["questions"].items():
            raw_marker = _value_at(row_values, column_index)
            parsed_marker = _parse_correctness_marker(raw_marker)
            if parsed_marker is None:
                plan.errors.append(
                    _error(
                        row_number,
                        f"question_{question_number}",
                        f"{question_number}번은 빈칸/O(정답) 또는 X(오답)로 입력해 주세요.",
                    )
                )
                marker_error = True
                continue
            correctness[question_number] = parsed_marker
        if marker_error:
            continue

        used_enrollment_ids.add(candidate.enrollment_id)
        correct_count = sum(1 for is_correct in correctness.values() if is_correct)
        wrong_numbers = tuple(
            sorted(number for number, is_correct in correctness.items() if not is_correct)
        )
        total_score, max_score = _score_row(
            exam=exam,
            questions=questions,
            correctness=correctness,
        )
        plan.rows.append(
            PlannedRow(
                source_row=row_number,
                candidate=candidate,
                correctness=correctness,
                correct_count=correct_count,
                wrong_question_numbers=wrong_numbers,
                total_score=total_score,
                max_score=max_score,
                will_overwrite=candidate.enrollment_id in existing_enrollment_ids,
            )
        )

    if not plan.rows and not plan.errors:
        plan.errors.append(_error(None, "rows", "반영할 학생 행을 찾지 못했습니다."))
    overwrite_count = sum(1 for row in plan.rows if row.will_overwrite)
    if overwrite_count:
        plan.warnings.append(
            f"기존 결과가 있는 {overwrite_count}명은 이번 엑셀의 문항별 정오로 갱신됩니다."
        )
    return plan


@transaction.atomic
def apply_exam_result_import(*, plan: ImportPlan) -> dict[str, Any]:
    if not plan.can_apply:
        raise ExamResultWorkbookError("오류가 있는 엑셀은 반영할 수 없습니다.")

    exam = plan.exam
    question_by_number = {question.number: question for question in plan.questions}
    now = timezone.now()

    for planned_row in plan.rows:
        enrollment_id = int(planned_row.candidate.enrollment_id)
        validate_exam_enrollment_assigned(exam, enrollment_id)
        enrollment = get_locked_enrollment_for_tenant(
            enrollment_id=enrollment_id,
            tenant=exam.tenant,
        )
        if enrollment is None:
            raise ExamResultWorkbookError(
                f"{planned_row.source_row}행 학생의 수강 정보를 찾을 수 없습니다."
            )

        result, attempt = _locked_result_and_attempt(
            exam=exam,
            enrollment=enrollment,
            initial_total=planned_row.total_score,
            initial_max=planned_row.max_score,
            now=now,
        )
        if attempt.status == "grading":
            raise ExamResultWorkbookError(
                f"{planned_row.source_row}행 학생은 현재 채점 중이라 반영할 수 없습니다."
            )

        objective_score = 0.0
        item_total = 0.0
        for question_number, is_correct in planned_row.correctness.items():
            question = question_by_number[question_number]
            earned = question.max_score if is_correct else 0.0
            item_total += earned
            if question.kind == "choice":
                objective_score += earned

            existing_item = (
                ResultItem.objects.select_for_update()
                .filter(result=result, question_id=question.question_id)
                .first()
            )
            changed = (
                existing_item is None
                or bool(existing_item.is_correct) != bool(is_correct)
                or abs(float(existing_item.score or 0.0) - float(earned)) > 0.0001
                or abs(float(existing_item.max_score or 0.0) - float(question.max_score)) > 0.0001
            )
            if changed:
                ResultFact.objects.create(
                    target_type="exam",
                    target_id=int(exam.id),
                    enrollment_id=enrollment_id,
                    submission_id=0,
                    attempt_id=int(attempt.id),
                    question_id=question.question_id,
                    answer="",
                    is_correct=is_correct,
                    score=float(earned),
                    max_score=float(question.max_score),
                    source="excel_import",
                    meta={
                        "excel_import": True,
                        "filename": plan.filename,
                        "source_row": planned_row.source_row,
                        "imported_at": now.isoformat(),
                    },
                )
            ResultItem.objects.update_or_create(
                result=result,
                question_id=question.question_id,
                defaults={
                    "answer": "",
                    "is_correct": is_correct,
                    "score": float(earned),
                    "max_score": float(question.max_score),
                    "source": "excel_import",
                },
            )

        objective_adjustment, total_adjustment = _score_adjustments(
            exam=exam,
            questions=plan.questions,
        )
        total_score = round(item_total + total_adjustment, 2)
        objective_score = round(objective_score + objective_adjustment, 2)
        result.attempt = attempt
        result.objective_score = objective_score
        result.total_score = total_score
        result.max_score = float(planned_row.max_score)
        result.submitted_at = now
        result.save(
            update_fields=[
                "attempt",
                "objective_score",
                "total_score",
                "max_score",
                "submitted_at",
                "updated_at",
            ]
        )

        meta = dict(attempt.meta or {}) if isinstance(attempt.meta, dict) else {}
        meta["total_score"] = total_score
        meta["max_score"] = float(planned_row.max_score)
        meta["synced_from_result"] = True
        meta["last_excel_import"] = {
            "filename": plan.filename,
            "source_row": planned_row.source_row,
            "imported_at": now.isoformat(),
        }
        if int(attempt.attempt_index) == 1 and not isinstance(meta.get("initial_snapshot"), dict):
            meta["initial_snapshot"] = {
                "total_score": total_score,
                "max_score": float(planned_row.max_score),
                "submitted_at": now.isoformat(),
                "source": "excel_result_import",
            }
        attempt.meta = meta
        attempt.status = "done"
        attempt.save(update_fields=["meta", "status", "updated_at"])

    exam_id = int(exam.id)

    def _dispatch_progress() -> None:
        try:
            dispatch_progress_pipeline(exam_id=exam_id)
        except Exception:
            logger.exception(
                "progress pipeline dispatch failed after excel result import (exam=%s)",
                exam_id,
            )

    transaction.on_commit(_dispatch_progress)
    return plan.as_payload(applied=True)


def _question_specs(*, exam: Any, tenant: Any) -> list[QuestionSpec]:
    try:
        sheet = resolve_omr_sheet_for_exam(
            tenant=tenant,
            exam_id=int(exam.id),
            requested_sheet_id=None,
        )
    except ValueError as exc:
        raise ExamResultWorkbookError("시험 문항을 먼저 등록해 주세요.") from exc

    questions = get_result_import_questions(sheet=sheet)
    if not questions:
        raise ExamResultWorkbookError("시험 문항을 먼저 등록해 주세요.")

    score_shape = get_exam_score_shape(exam)
    specs = [
        QuestionSpec(
            question_id=question.question_id,
            number=int(question.number),
            kind=str(score_shape.question_kind(question.question_id) or "choice"),
            max_score=float(
                score_shape.question_max_score(question.question_id, question.score)
            ),
        )
        for question in questions
    ]
    if len({question.number for question in specs}) != len(specs):
        raise ExamResultWorkbookError("시험 문항 번호가 중복되어 있습니다.")
    return specs


def _exam_candidates(*, exam: Any, tenant: Any) -> list[Candidate]:
    records = get_result_import_candidates(
        exam_id=int(exam.id),
        tenant=tenant,
    )
    return [_candidate_from_record(record) for record in records]


def _candidate_from_record(record: ResultImportCandidateRecord) -> Candidate:
    return Candidate(
        enrollment_id=record.enrollment_id,
        student_name=record.student_name,
        student_phone=record.student_phone,
        parent_phone=record.parent_phone,
        school=record.school,
        lecture_id=record.lecture_id,
        lecture_title=record.lecture_title,
        lecture_color=record.lecture_color,
        lecture_chip_label=record.lecture_chip_label,
    )


def _load_first_worksheet(workbook_bytes: bytes):
    if not workbook_bytes:
        raise ExamResultWorkbookError("비어 있는 파일입니다.")
    if len(workbook_bytes) > MAX_UPLOAD_BYTES:
        raise ExamResultWorkbookError("엑셀 파일은 10MB 이하만 업로드할 수 있습니다.")
    try:
        with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as archive:
            total_size = sum(info.file_size for info in archive.infolist())
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise ExamResultWorkbookError("압축을 푼 엑셀 파일이 너무 큽니다.")
    except zipfile.BadZipFile as exc:
        raise ExamResultWorkbookError("올바른 .xlsx 파일이 아닙니다.") from exc

    try:
        workbook = load_workbook(
            io.BytesIO(workbook_bytes),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except Exception as exc:
        raise ExamResultWorkbookError("엑셀 파일을 열 수 없습니다.") from exc
    if not workbook.worksheets:
        raise ExamResultWorkbookError("엑셀 시트를 찾을 수 없습니다.")
    worksheet = workbook.worksheets[0]
    if worksheet.max_row > MAX_ROWS or worksheet.max_column > MAX_COLUMNS:
        raise ExamResultWorkbookError("엑셀은 2,000행·600열 이하로 작성해 주세요.")
    return worksheet


def _find_header(worksheet, questions: list[QuestionSpec]) -> tuple[int, dict[str, Any]]:
    expected_numbers = {question.number for question in questions}
    for row_number, row in enumerate(
        worksheet.iter_rows(
            min_row=1,
            max_row=min(worksheet.max_row, MAX_HEADER_SCAN_ROWS),
            max_col=worksheet.max_column,
            values_only=True,
        ),
        start=1,
    ):
        columns: dict[str, Any] = {"questions": {}}
        duplicate_questions: set[int] = set()
        for index, value in enumerate(row):
            normalized = _normalize_header(value)
            if normalized in _ENROLLMENT_HEADERS and "enrollment" not in columns:
                columns["enrollment"] = index
            elif normalized in _NAME_HEADERS and "name" not in columns:
                columns["name"] = index
            elif normalized in _STUDENT_PHONE_HEADERS and "student_phone" not in columns:
                columns["student_phone"] = index
            elif normalized in _PARENT_PHONE_HEADERS and "parent_phone" not in columns:
                columns["parent_phone"] = index

            question_number = _question_number_from_header(value)
            if question_number is not None:
                if question_number in columns["questions"]:
                    duplicate_questions.add(question_number)
                else:
                    columns["questions"][question_number] = index

        has_identity = any(
            key in columns
            for key in ("enrollment", "name", "student_phone", "parent_phone")
        )
        has_expected_question = bool(expected_numbers & set(columns["questions"]))
        if not has_identity or not has_expected_question:
            continue
        if duplicate_questions:
            duplicated = ", ".join(map(str, sorted(duplicate_questions)))
            raise ExamResultWorkbookError(f"문항 열이 중복되어 있습니다: {duplicated}번")
        return row_number, columns
    raise ExamResultWorkbookError(
        "이름·연락처(또는 수강등록ID)와 1, 2, 3… 문항 번호가 있는 헤더 행을 찾지 못했습니다."
    )


def _match_candidate(
    *,
    row_values: tuple[Any, ...],
    columns: dict[str, Any],
    by_id: dict[int, Candidate],
    by_name: dict[str, list[Candidate]],
    by_phone: dict[str, list[Candidate]],
) -> tuple[Candidate | None, str | None]:
    enrollment_raw = _value_at(row_values, columns.get("enrollment"))
    name = str(_value_at(row_values, columns.get("name")) or "").strip()
    normalized_name = _normalize_name(name)
    phone_values = [
        _normalize_phone(_value_at(row_values, columns.get("student_phone"))),
        _normalize_phone(_value_at(row_values, columns.get("parent_phone"))),
    ]
    phones = [phone for phone in phone_values if phone]

    if _has_value(enrollment_raw):
        enrollment_id = _positive_int(enrollment_raw)
        candidate = by_id.get(enrollment_id or 0)
        if candidate is None:
            return None, "수강등록ID가 이 시험의 학생과 일치하지 않습니다."
        if normalized_name and normalized_name != _normalize_name(candidate.student_name):
            return None, "수강등록ID와 학생 이름이 서로 다릅니다."
        return candidate, None

    phone_matches: dict[int, Candidate] = {}
    for phone in phones:
        for candidate in by_phone.get(phone, []):
            phone_matches[candidate.enrollment_id] = candidate
    if phone_matches:
        matches = list(phone_matches.values())
        if normalized_name:
            named_matches = [
                candidate
                for candidate in matches
                if _normalize_name(candidate.student_name) == normalized_name
            ]
            if not named_matches:
                return None, "연락처와 학생 이름이 서로 다릅니다."
            matches = named_matches
        if len(matches) == 1:
            return matches[0], None
        return None, "연락처가 같은 학생이 여러 명입니다. 수강등록ID를 함께 입력해 주세요."

    if phones:
        return None, "이 시험에 등록된 학생과 연락처가 일치하지 않습니다."

    if normalized_name:
        matches = by_name.get(normalized_name, [])
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, "이름이 같은 학생이 여러 명입니다. 연락처 또는 수강등록ID가 필요합니다."
    return None, "이 시험에 등록된 학생과 이름·연락처가 일치하지 않습니다."


def _score_row(
    *,
    exam: Any,
    questions: list[QuestionSpec],
    correctness: dict[int, bool],
) -> tuple[float, float]:
    item_total = sum(
        question.max_score
        for question in questions
        if correctness.get(question.number, False)
    )
    _, total_adjustment = _score_adjustments(exam=exam, questions=questions)
    score_shape = get_exam_score_shape(exam)
    calculated_max = sum(question.max_score for question in questions) + total_adjustment
    max_score = float(score_shape.total_max_score or calculated_max or exam.max_score or 0.0)
    return round(item_total + total_adjustment, 2), round(max_score, 2)


def _score_adjustments(*, exam: Any, questions: list[QuestionSpec]) -> tuple[float, float]:
    score_shape = get_exam_score_shape(exam)
    adjustment = get_score_adjustment_from_answers(
        get_answer_key_answers(template_exam_id=score_shape.template_exam_id)
    )
    has_choice = any(question.kind == "choice" for question in questions)
    has_essay = any(question.kind == "essay" for question in questions)
    objective = float(adjustment.objective if has_choice else 0.0)
    total = objective + float(adjustment.subjective if has_essay else 0.0)
    return objective, total


def _locked_result_and_attempt(
    *,
    exam: Any,
    enrollment: Any,
    initial_total: float,
    initial_max: float,
    now,
) -> tuple[Result, ExamAttempt]:
    result = (
        Result.objects.select_for_update()
        .filter(
            target_type="exam",
            target_id=int(exam.id),
            enrollment_id=int(enrollment.id),
        )
        .first()
    )
    attempt = None
    if result and result.attempt_id:
        attempt = ExamAttempt.objects.select_for_update().filter(id=result.attempt_id).first()
    if attempt is None:
        attempt = (
            ExamAttempt.objects.select_for_update()
            .filter(
                exam_id=int(exam.id),
                enrollment_id=int(enrollment.id),
                is_representative=True,
            )
            .first()
        )
    if attempt is None:
        attempts = ExamAttempt.objects.select_for_update().filter(
            exam_id=int(exam.id),
            enrollment_id=int(enrollment.id),
        )
        last_index = attempts.aggregate(Max("attempt_index")).get("attempt_index__max") or 0
        attempts.filter(is_representative=True).update(is_representative=False)
        attempt = ExamAttempt.objects.create(
            exam_id=int(exam.id),
            enrollment_id=int(enrollment.id),
            submission_id=0,
            attempt_index=int(last_index) + 1,
            is_retake=bool(last_index),
            is_representative=True,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": float(initial_total),
                    "max_score": float(initial_max),
                    "submitted_at": now.isoformat(),
                    "source": "excel_result_import",
                }
            },
        )
    elif not attempt.is_representative:
        ExamAttempt.objects.filter(
            exam_id=int(exam.id),
            enrollment_id=int(enrollment.id),
            is_representative=True,
        ).exclude(id=attempt.id).update(is_representative=False)
        attempt.is_representative = True
        attempt.save(update_fields=["is_representative", "updated_at"])

    if result is None:
        result = Result.objects.create(
            target_type="exam",
            target_id=int(exam.id),
            enrollment=enrollment,
            attempt=attempt,
            total_score=0.0,
            max_score=float(initial_max),
            objective_score=0.0,
        )
    elif result.attempt_id != attempt.id:
        result.attempt = attempt
        result.save(update_fields=["attempt", "updated_at"])
    return result, attempt


def _parse_correctness_marker(value: Any) -> bool | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)) and float(value) in {0.0, 1.0}:
        return bool(int(value))
    normalized = "".join(str(value).strip().lower().split())
    if normalized in _CORRECT_MARKERS:
        return True
    if normalized in _WRONG_MARKERS:
        return False
    return None


def _question_number_from_header(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        number = int(value)
        return number if number > 0 else None
    text = str(value).strip().lower()
    match = re.fullmatch(r"(?:q|문항)?\s*0*(\d+)\s*(?:번)?", text)
    if not match:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


def _normalize_header(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").strip().lower())


def _safe_excel_text(value: Any) -> str:
    text = str(value or "")
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _normalize_name(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _normalize_phone(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) == 10 and digits.startswith("10"):
        digits = f"0{digits}"
    return digits


def _positive_int(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _value_at(values: tuple[Any, ...], index: int | None) -> Any:
    if index is None or index < 0 or index >= len(values):
        return None
    return values[index]


def _has_value(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _error(row: int | None, field: str, message: str) -> dict[str, Any]:
    return {"row": row, "field": field, "message": message}
