from __future__ import annotations

import io
import math
from statistics import median, pstdev, quantiles
from typing import Any

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.data_source import AxDataSource, NumData, NumVal, StrData, StrRef, StrVal
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.line import LineProperties
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from apps.domains.results.models import ResultFact
from apps.domains.results.services.exam_analysis_metrics import load_exam_analysis, score_rate
from apps.domains.results.services.exam_result_excel_import import ExamResultWorkbookError
from apps.domains.results.services.question_stats_service import QuestionStatsService
from apps.support.results.exam_result_excel_import_dependencies import get_result_import_questions
from apps.support.omr.sheet_resolver import resolve_omr_sheet_for_exam


_NAVY = "172554"
_BLUE = "2563EB"
_BLUE_SOFT = "E8F0FF"
_SLATE = "334155"
_MUTED = "64748B"
_BORDER = "CBD5E1"
_GREEN = "15803D"
_GREEN_SOFT = "DCFCE7"
_AMBER = "B45309"
_AMBER_SOFT = "FEF3C7"
_RED = "B91C1C"
_RED_SOFT = "FEE2E2"
_WHITE = "FFFFFF"

_THIN_BORDER = Border(
    left=Side(style="thin", color=_BORDER),
    right=Side(style="thin", color=_BORDER),
    top=Side(style="thin", color=_BORDER),
    bottom=Side(style="thin", color=_BORDER),
)


def _safe_excel_text(value: Any) -> str:
    text = str(value or "")
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _top_ten_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    count = max(1, math.ceil(len(values) * 0.1))
    return _mean(sorted(values, reverse=True)[:count])


def _set_title(sheet, title: str, subtitle: str, *, last_column: int) -> None:
    sheet.merge_cells(start_row=1, start_column=1, end_row=2, end_column=last_column)
    cell = sheet.cell(1, 1, _safe_excel_text(title))
    cell.font = Font(size=19, bold=True, color=_WHITE)
    cell.fill = PatternFill("solid", fgColor=_NAVY)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    sheet.row_dimensions[1].height = 25
    sheet.row_dimensions[2].height = 18
    sheet.merge_cells(start_row=3, start_column=1, end_row=3, end_column=last_column)
    cell = sheet.cell(3, 1, _safe_excel_text(subtitle))
    cell.font = Font(size=10, color=_MUTED)
    cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[3].height = 22


def _section_heading(sheet, row: int, title: str, *, last_column: int) -> None:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_column)
    cell = sheet.cell(row, 1, title)
    cell.font = Font(size=12, bold=True, color=_NAVY)
    cell.fill = PatternFill("solid", fgColor=_BLUE_SOFT)
    cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[row].height = 23


def _table_header(sheet, row: int, headers: list[str]) -> None:
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row, column, header)
        cell.font = Font(size=10, bold=True, color=_WHITE)
        cell.fill = PatternFill("solid", fgColor=_SLATE)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _THIN_BORDER
    sheet.row_dimensions[row].height = 25


def _style_data_table(sheet, *, start_row: int, end_row: int, end_column: int) -> None:
    for row in sheet.iter_rows(
        min_row=start_row,
        max_row=end_row,
        min_col=1,
        max_col=end_column,
    ):
        for cell in row:
            cell.border = _THIN_BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if cell.row % 2 == 0 and cell.fill.fill_type is None:
                cell.fill = PatternFill("solid", fgColor="F8FAFC")


def _finalize_sheet(sheet, widths: list[float], *, freeze: str | None = None) -> None:
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.sheet_view.showGridLines = False
    if freeze:
        sheet.freeze_panes = freeze
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.outlinePr.summaryBelow = True


def _add_distribution_chart(sheet, header, labels, counts):
    chart = BarChart()
    chart.type = "bar"
    chart.title = "구간별 응시 인원"
    chart.legend = None
    chart.width = 13.2
    chart.height = 5.8
    chart.gapWidth = 55
    largest = max(counts)
    major_unit = max(1, math.ceil(largest / 5))
    chart.y_axis.scaling.min = 0
    chart.y_axis.scaling.max = (math.ceil(largest / major_unit) + 1) * major_unit
    chart.y_axis.majorUnit = major_unit
    chart.y_axis.majorGridlines = ChartLines(spPr=GraphicalProperties(ln=LineProperties(solidFill="E2E8F0")))
    chart.x_axis.scaling.orientation = "maxMin"
    chart.y_axis.crosses = "max"
    chart.add_data(Reference(sheet, min_col=2, min_row=header, max_row=header + len(labels)), titles_from_data=True)
    categories = Reference(sheet, min_col=1, min_row=header + 1, max_row=header + len(labels))
    series = chart.series[0]
    series.val.numRef.numCache = NumData(
        ptCount=len(counts), pt=[NumVal(idx=i, v=value) for i, value in enumerate(counts)],
    )
    series.cat = AxDataSource(strRef=StrRef(
        f=str(categories),
        strCache=StrData(ptCount=len(labels), pt=[StrVal(idx=i, v=label) for i, label in enumerate(labels)]),
    ))
    series.graphicalProperties.solidFill = _BLUE
    series.graphicalProperties.line.noFill = True
    chart.dLbls = DataLabelList(
        showVal=True, showCatName=False, showSerName=False, showLegendKey=False,
        showPercent=False, showBubbleSize=False, dLblPos="outEnd", numFmt='0"명"',
    )
    sheet.add_chart(chart, "D14")


def _add_trends_sheet(workbook, exam_title, insights):
    sheet = workbook.create_sheet("성적 추이")
    threshold = insights["trend_threshold_pp"]
    _set_title(
        sheet, f"{exam_title} · 성적 추이",
        f"같은 강의·반·과목의 1차 득점률 · 두 비교값이 같은 방향으로 {threshold}%p 이상이면 표시",
        last_column=9,
    )
    _table_header(sheet, 5, [
        "학생", "강의", "이번 시험", "직전 차시", "이전 평균", "비교 시험", "직전 대비", "평균 대비", "변화",
    ])
    trends = insights["trends"]
    for row, trend in enumerate(trends, start=6):
        values = [
            _safe_excel_text(trend["student_name"]), _safe_excel_text(trend["lecture_title"]),
            trend["current_rate"], trend["previous_session_rate"], trend["cumulative_rate"],
            trend["prior_exam_count"], trend["previous_delta_pp"], trend["cumulative_delta_pp"],
            {"up": "상승", "down": "하락"}.get(trend["signal"]),
        ]
        for column, value in enumerate(values, start=1):
            sheet.cell(row, column, value)
        for column in (3, 4, 5):
            sheet.cell(row, column).number_format = '0.0"%"'
        for column in (7, 8):
            sheet.cell(row, column).number_format = '+0.0"%p";-0.0"%p";0.0"%p"'
        if trend["signal"]:
            up = trend["signal"] == "up"
            sheet.cell(row, 9).fill = PatternFill("solid", fgColor=_GREEN_SOFT if up else _RED_SOFT)
            sheet.cell(row, 9).font = Font(bold=True, color=_GREEN if up else _RED)
    if trends:
        _style_data_table(sheet, start_row=6, end_row=5 + len(trends), end_column=9)
        sheet.auto_filter.ref = f"A5:I{5 + len(trends)}"
    else:
        sheet.merge_cells("A6:I6")
        sheet["A6"] = "비교할 수 있는 이전 시험 성적이 없습니다."
    sheet.merge_cells("A4:I4")
    sheet["A4"] = "직전 차시 점수가 없으면 이전 평균만 비교합니다."
    sheet["A4"].font = Font(size=10, color=_MUTED)
    sheet.row_dimensions[4].height = 19
    _finalize_sheet(sheet, [15, 26, 13, 13, 13, 12, 14, 14, 11], freeze="C6")


def build_exam_analysis_export(*, exam: Any, tenant: Any) -> bytes:
    """Build a teacher-facing workbook from the current representative exam state."""
    analysis = load_exam_analysis(exam=exam, tenant=tenant, include_items=True)
    candidate_by_id = analysis.candidates
    candidates = list(candidate_by_id.values())
    if not candidates:
        raise ExamResultWorkbookError("이 시험에 등록된 학생이 없습니다.")

    try:
        sheet = resolve_omr_sheet_for_exam(
            tenant=tenant,
            exam_id=int(exam.id),
            requested_sheet_id=None,
        )
    except ValueError:
        sheet = None
    questions = get_result_import_questions(sheet=sheet) if sheet is not None else []
    question_by_id = {int(question.question_id): question for question in questions}

    result_by_id = analysis.results
    results = list(result_by_id.values())
    analysis_rows = analysis.score_by_enrollment
    initial_scores = {enrollment_id: row.initial for enrollment_id, row in analysis_rows.items()}
    initial_attempt_ids = {
        score.attempt_id for score in initial_scores.values() if score.attempt_id is not None
    }
    fact_items_by_enrollment: dict[int, dict[int, Any]] = {}
    for fact in ResultFact.objects.current().filter(
        attempt_id__in=initial_attempt_ids,
        enrollment_id__in=candidate_by_id,
    ).order_by("-id"):
        fact_items_by_enrollment.setdefault(int(fact.enrollment_id), {}).setdefault(
            int(fact.question_id),
            fact,
        )

    def initial_items(result: Any | None) -> list[Any]:
        if result is None:
            return []
        enrollment_id = int(result.enrollment_id)
        initial_score = initial_scores[enrollment_id]
        if result.attempt_id == initial_score.attempt_id:
            return list(result.items.all())
        return list(fact_items_by_enrollment.get(enrollment_id, {}).values())

    max_score = float(exam.max_score or 100.0)

    def candidate_pass_score(candidate: Any) -> float:
        return analysis.pass_scores_by_lecture.get(candidate.lecture_id, float(exam.pass_score or 0.0))

    def analysis_score(result: Any) -> float:
        return initial_scores[result.enrollment_id].total_score

    scored_results = [result for result in results if analysis_rows[result.enrollment_id].status == "DONE"]
    score_by_enrollment = {
        int(result.enrollment_id): analysis_score(result)
        for result in scored_results
    }
    scores = list(score_by_enrollment.values())
    analysis_rankings: dict[int, dict[str, float | int]] = {}
    previous_score: float | None = None
    current_rank = 0
    for position, (enrollment_id, score) in enumerate(
        sorted(score_by_enrollment.items(), key=lambda item: (-item[1], item[0])),
        start=1,
    ):
        if previous_score is None or score != previous_score:
            current_rank = position
            previous_score = score
        analysis_rankings[enrollment_id] = {
            "rank": current_rank,
            "ranking_score": score,
        }
    scored_attempt_ids = [
        int(initial_scores[int(result.enrollment_id)].attempt_id)
        for result in scored_results
        if initial_scores[int(result.enrollment_id)].attempt_id is not None
    ]
    legacy_scored_enrollment_ids = [
        int(result.enrollment_id)
        for result in scored_results
        if initial_scores[result.enrollment_id].attempt_id is None and result.attempt_id is None
    ]
    question_stats = QuestionStatsService.per_question_stats(
        exam_id=int(exam.id),
        attempt_ids=scored_attempt_ids,
        legacy_enrollment_ids=legacy_scored_enrollment_ids,
    )
    question_stats.sort(
        key=lambda row: (
            float(row.get("accuracy") or 0.0),
            -int(row.get("attempts") or 0),
            int(row.get("question_number") or 0),
        )
    )
    scored_with_criterion = [
        (analysis_score(result), candidate_pass_score(candidate_by_id[int(result.enrollment_id)]))
        for result in scored_results
        if candidate_pass_score(candidate_by_id[int(result.enrollment_id)]) > 0
    ]
    criterion_count = len(scored_with_criterion)
    has_pass_criterion = criterion_count > 0
    pass_count = sum(
        1 for score, criterion in scored_with_criterion if score >= criterion
    )
    fail_count = max(criterion_count - pass_count, 0)
    pass_rate = (
        pass_count / criterion_count
        if has_pass_criterion
        else 0.0
    )
    score_rates = [
        score_rate(analysis_score(result), initial_scores[result.enrollment_id].max_score)
        for result in scored_results
    ]
    quartiles = quantiles(scores, n=4, method="inclusive") if len(scores) >= 4 else None
    cutoff = analysis.insights["recommended_cutoff"]
    below_cutoff = analysis.insights["below_recommended_count"]

    generated_at = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    workbook = Workbook()
    workbook.properties.title = f"{exam.title} 수업 분석 리포트"
    workbook.properties.subject = "1차 확정 성적과 문항 통계"
    workbook.properties.creator = "학원플러스"

    overview = workbook.active
    overview.title = "시험 요약"
    _set_title(
        overview,
        f"{exam.title} · 수업 분석 리포트",
        f"{getattr(exam, 'subject', '') or '과목 미지정'} · 생성 {generated_at}",
        last_column=8,
    )
    _section_heading(overview, 5, "핵심 지표", last_column=8)
    metrics = [
        ("응시", f"{len(scores)}명"),
        ("미응시·미채점", f"{len(candidates) - len(scores)}명"),
        ("평균", f"{_mean(scores):.1f}/{max_score:g}" if scores else "—"),
        ("상위 10% 평균", f"{_top_ten_mean(scores):.1f}/{max_score:g}" if scores else "—"),
        ("최고점", f"{max(scores):g}/{max_score:g}" if scores else "—"),
        ("중앙값", f"{median(scores):.1f}점" if scores else "—"),
        ("표준편차", f"{pstdev(scores):.1f}점" if scores else "—"),
        (
            "1차 합격",
            (
                f"{pass_count}명 · {pass_rate * 100:.0f}%"
                if has_pass_criterion and criterion_count == len(scores)
                else f"{pass_count}/{criterion_count}명 · {pass_rate * 100:.0f}%"
                if has_pass_criterion
                else "기준 미설정"
            ),
        ),
        ("추천 커트라인", f"{cutoff}점" if cutoff is not None else "—"),
        ("추천선 미만", f"{below_cutoff}/{len(scores)}명" if below_cutoff is not None else "—"),
        ("중간 50% 점수", f"{quartiles[0]:.1f}–{quartiles[2]:.1f}점" if quartiles else "—"),
        ("성적 추이 비교 가능", f"{len(analysis.insights['trends'])}명"),
    ]
    for index, (label, value) in enumerate(metrics):
        column = (index % 4) * 2 + 1
        row = 6 + (index // 4) * 2
        overview.merge_cells(start_row=row, start_column=column, end_row=row, end_column=column + 1)
        overview.merge_cells(start_row=row + 1, start_column=column, end_row=row + 1, end_column=column + 1)
        overview.cell(row, column, label).font = Font(size=9, bold=True, color=_MUTED)
        overview.cell(row + 1, column, value).font = Font(size=12, bold=True, color=_NAVY)
        for row_index in (row, row + 1):
            for target_column in (column, column + 1):
                cell = overview.cell(row_index, target_column)
                cell.fill = PatternFill("solid", fgColor="FFFFFF")
                cell.border = _THIN_BORDER
                cell.alignment = Alignment(horizontal="center", vertical="center")

    _section_heading(overview, 13, "점수 분포 · 만점 대비 구간", last_column=8)
    distribution_header = 14
    _table_header(overview, distribution_header, ["만점 대비", "인원", "비율"])
    bands = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    labels, counts = [], []
    for offset, (lower, upper) in enumerate(bands, start=1):
        count = sum(
            1 for rate in score_rates
            if (rate >= lower if lower == 0 else rate > lower) and rate <= upper
        )
        label = f"{lower}–{upper}%" if lower == 0 else f">{lower}–{upper}%"
        labels.append(label)
        counts.append(count)
        row = distribution_header + offset
        overview.cell(row, 1, label)
        overview.cell(row, 2, count)
        overview.cell(row, 3, count / len(scores) if scores else None).number_format = "0.0%"
        overview.row_dimensions[row].height = 23
    _style_data_table(overview, start_row=15, end_row=19, end_column=3)
    if scores:
        _add_distribution_chart(overview, distribution_header, labels, counts)
    else:
        overview.merge_cells("D15:H19")
        overview["D15"] = "확정된 1차 점수가 없습니다."
        overview["D15"].alignment = Alignment(horizontal="center", vertical="center")

    _section_heading(overview, 23, "정답률이 낮은 문항", last_column=8)
    _table_header(overview, 24, ["문항", "정답률", "정답 수", "응시 수"])
    weakest = question_stats[:5]
    for offset, row_data in enumerate(weakest, start=1):
        row = 24 + offset
        overview.cell(row, 1, f"{int(row_data['question_number'])}번")
        overview.cell(row, 2, float(row_data.get("accuracy") or 0.0)).number_format = "0.0%"
        overview.cell(row, 3, int(row_data.get("correct") or 0))
        overview.cell(row, 4, int(row_data.get("attempts") or 0))
    if weakest:
        _style_data_table(overview, start_row=25, end_row=24 + len(weakest), end_column=4)
    else:
        overview.merge_cells("A25:D26")
        overview["A25"] = "집계할 문항별 채점 기록이 없습니다."
        overview["A25"].alignment = Alignment(vertical="center")

    _finalize_sheet(overview, [16, 15, 14, 15, 16, 16, 16, 16], freeze="A5")
    overview.print_area = "A1:H29"
    overview.page_setup.orientation = overview.ORIENTATION_LANDSCAPE
    overview.page_setup.paperSize = overview.PAPERSIZE_A4
    overview.page_setup.fitToHeight = 1
    overview.print_options.horizontalCentered = True
    overview.page_margins.top = overview.page_margins.bottom = 0.45

    priority_sheet = workbook.create_sheet("문항 우선순위")
    _set_title(
        priority_sheet,
        f"{exam.title} · 문항 우선순위",
        "확정된 1차 문항 기록 · 정답률 오름차순",
        last_column=7,
    )
    headers = ["순서", "문항", "정답률", "정답 수", "응시 수", "평균 점수", "문항 배점"]
    _table_header(priority_sheet, 5, headers)
    for offset, row_data in enumerate(question_stats, start=1):
        accuracy = float(row_data.get("accuracy") or 0.0)
        values = [
            offset,
            int(row_data["question_number"]),
            accuracy,
            int(row_data.get("correct") or 0),
            int(row_data.get("attempts") or 0),
            float(row_data.get("avg_score") or 0.0),
            float(row_data.get("max_possible_score") or 0.0),
        ]
        for column, value in enumerate(values, start=1):
            priority_sheet.cell(5 + offset, column, value)
        priority_sheet.cell(5 + offset, 3).number_format = "0.0%"
    if question_stats:
        _style_data_table(priority_sheet, start_row=6, end_row=5 + len(question_stats), end_column=7)
        priority_sheet.conditional_formatting.add(
            f"C6:C{5 + len(question_stats)}",
            ColorScaleRule(
                start_type="num", start_value=0, start_color=_RED_SOFT,
                mid_type="num", mid_value=0.5, mid_color=_AMBER_SOFT,
                end_type="num", end_value=1, end_color=_GREEN_SOFT,
            ),
        )
        priority_sheet.auto_filter.ref = f"A5:G{5 + len(question_stats)}"
    _finalize_sheet(priority_sheet, [12, 10, 12, 11, 11, 13, 12], freeze="A6")

    _add_trends_sheet(workbook, exam.title, analysis.insights)

    ranked_sheet = workbook.create_sheet("학생별 등수")
    _set_title(
        ranked_sheet,
        f"{exam.title} · 학생별 등수",
        "서술형 확정분을 포함한 1차 확정 점수와 등수입니다. 공동 등수는 같은 등수로 표시하고 다음 등수는 인원만큼 건너뜁니다.",
        last_column=13,
    )
    ranked_headers = [
        "등수", "학교", "이름", "강의", "1차 점수", "만점", "득점률", "평균 대비",
        "합격 기준", "판정", "결과 상태", "오답 문항", "사이트 최종 저장",
    ]
    _table_header(ranked_sheet, 5, ranked_headers)
    average_score = _mean(scores)
    ranked_rows: list[tuple[tuple[Any, ...], int, list[Any]]] = []
    for candidate in candidates:
        enrollment_id = int(candidate.enrollment_id)
        result = result_by_id.get(enrollment_id)
        analysis_row = analysis_rows.get(enrollment_id)
        analysis_status = analysis_row.status if analysis_row else "MISSING"
        scored = analysis_status == "DONE"
        rank_info = analysis_rankings.get(enrollment_id, {}) if scored else {}
        score = analysis_score(result) if scored else None
        result_max = (analysis_row.initial.max_score or max_score) if analysis_row else max_score
        items = initial_items(result)
        wrong_numbers = sorted(
            question_by_id[int(item.question_id)].number
            for item in items
            if not bool(item.is_correct) and int(item.question_id) in question_by_id
        )
        status = {
            "MISSING": "미응시·미채점",
            "NOT_SUBMITTED": "미응시",
            "PROCESSING": "채점 중",
            "PARTIAL": "채점 미확정",
            "FAILED": "채점 실패",
            "DONE": "완료",
        }[analysis_status]
        achievement = analysis_row.achievement if analysis_row else {}
        pass_score = candidate_pass_score(candidate)
        candidate_has_pass_criterion = pass_score > 0
        if score is None:
            verdict = "-"
        elif not candidate_has_pass_criterion:
            verdict = "기준 미설정"
        elif bool(achievement.get("remediated")):
            verdict = "보충 완료"
        else:
            verdict = "합격" if score >= pass_score else "보충 대상"
        values = [
            rank_info.get("rank"),
            _safe_excel_text(candidate.school),
            _safe_excel_text(candidate.student_name),
            _safe_excel_text(candidate.lecture_title),
            score,
            result_max,
            (score / result_max) if score is not None and result_max > 0 else None,
            (score - average_score) if score is not None and scores else None,
            pass_score,
            verdict,
            status,
            ", ".join(str(number) for number in wrong_numbers),
            (
                timezone.localtime(initial_scores[enrollment_id].recorded_at).strftime("%Y-%m-%d %H:%M")
                if result is not None and initial_scores[enrollment_id].recorded_at is not None
                else ""
            ),
        ]
        sort_key = (
            rank_info.get("rank") is None,
            rank_info.get("rank") or 10**9,
            _safe_excel_text(candidate.student_name),
            enrollment_id,
        )
        ranked_rows.append((sort_key, enrollment_id, values))
    ranked_rows.sort(key=lambda item: item[0])
    for offset, (_, _, values) in enumerate(ranked_rows, start=1):
        for column, value in enumerate(values, start=1):
            ranked_sheet.cell(5 + offset, column, value)
        ranked_sheet.cell(5 + offset, 7).number_format = "0.0%"
        ranked_sheet.cell(5 + offset, 8).number_format = "+0.0;-0.0;0.0"
        verdict_cell = ranked_sheet.cell(5 + offset, 10)
        if verdict_cell.value == "합격":
            verdict_cell.fill = PatternFill("solid", fgColor=_GREEN_SOFT)
            verdict_cell.font = Font(bold=True, color=_GREEN)
        elif verdict_cell.value == "보충 완료":
            verdict_cell.fill = PatternFill("solid", fgColor=_BLUE_SOFT)
            verdict_cell.font = Font(bold=True, color=_BLUE)
        elif verdict_cell.value == "보충 대상":
            verdict_cell.fill = PatternFill("solid", fgColor=_RED_SOFT)
            verdict_cell.font = Font(bold=True, color=_RED)
    _style_data_table(ranked_sheet, start_row=6, end_row=5 + len(ranked_rows), end_column=13)
    ranked_sheet.auto_filter.ref = f"A5:M{5 + len(ranked_rows)}"
    _finalize_sheet(ranked_sheet, [9, 16, 12, 19, 10, 10, 11, 12, 11, 13, 15, 22, 18], freeze="A6")

    answers_sheet = workbook.create_sheet("학생별 답안")
    answer_last_column = max(7 + len(questions), 8)
    _set_title(
        answers_sheet,
        f"{exam.title} · 학생별 답안",
        "1차 결과 기준입니다. 빨강은 오답, 초록은 정답이며 미입력은 복원 가능한 1차 채점 기록이 없는 문항입니다.",
        last_column=answer_last_column,
    )
    answer_headers = ["등수", "학교", "이름", "강의", "1차 점수", "만점", "판정"] + [
        f"{question.number}번" for question in questions
    ]
    _table_header(answers_sheet, 5, answer_headers)
    for offset, (_, enrollment_id, ranked_values) in enumerate(ranked_rows, start=1):
        result = result_by_id.get(enrollment_id)
        item_by_question = {
            int(item.question_id): item for item in initial_items(result)
        }
        base_values = ranked_values[:6] + [ranked_values[9]]
        for column, value in enumerate(base_values, start=1):
            answers_sheet.cell(5 + offset, column, value)
        for question_offset, question in enumerate(questions, start=8):
            item = item_by_question.get(int(question.question_id))
            cell = answers_sheet.cell(5 + offset, question_offset)
            if item is None:
                cell.value = "미입력"
                cell.font = Font(color=_MUTED)
            else:
                cell.value = _safe_excel_text(str(item.answer or "").strip() or "미입력")
                if bool(item.is_correct):
                    cell.fill = PatternFill("solid", fgColor=_GREEN_SOFT)
                    cell.font = Font(color=_GREEN)
                else:
                    cell.fill = PatternFill("solid", fgColor=_RED_SOFT)
                    cell.font = Font(color=_RED)
    _style_data_table(answers_sheet, start_row=6, end_row=5 + len(ranked_rows), end_column=len(answer_headers))
    answers_sheet.auto_filter.ref = f"A5:{get_column_letter(len(answer_headers))}{5 + len(ranked_rows)}"
    _finalize_sheet(
        answers_sheet,
        [9, 16, 12, 19, 10, 10, 13] + [11] * len(questions),
        freeze="H6",
    )

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()
