"""Read-only, on-demand teacher signals. Never creates events or sends messages."""
from collections import defaultdict
from datetime import date
from statistics import fmean

from django.db.models import Exists, F, OuterRef, Subquery

from apps.core.models import TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import ExamEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import HomeworkScore
from apps.domains.lectures.models import Session
from apps.domains.results.services.exam_analysis_metrics import _load_scores, score_rate, top_reference
from apps.domains.results.utils.result_queries import latest_results_for_targets_per_enrollment
from apps.domains.submissions.models import Submission
from apps.domains.video.models import AccessMode, Video, VideoAccess, VideoProgress
from apps.domains.video.policy import normalize_video_progress
from apps.domains.video.services.access_resolver import resolve_access_modes_prefetched


DEFAULTS = {
    "absence_streak": {"enabled": True, "threshold": 2},
    "homework_missing": {"enabled": True, "threshold": 2},
    "exam_drop": {"enabled": True, "threshold": 15},
    "recent_exam_drop": {"enabled": True, "threshold": 10},
    "top_reference_gap": {"enabled": True, "threshold": 15},
    "exam_rise": {"enabled": True, "threshold": 15},
    "homework_drop": {"enabled": True, "threshold": 15},
    "video_watch": {"enabled": True, "threshold": 80},
}


def attendance_signals(tenant, roster, today, threshold):
    assigned = SessionEnrollment.objects.filter(
        tenant=tenant, enrollment_id__in=roster, session__lecture_id=F("enrollment__lecture_id"),
        session__date__lte=today,
    ).annotate(mark=Subquery(Attendance.objects.filter(
        tenant=tenant, session_id=OuterRef("session_id"), enrollment_id=OuterRef("enrollment_id")
    ).values("status")[:1])).order_by("enrollment_id", "-session__date", "-session__order", "-session_id")
    streaks = {}
    stopped = set()
    for eid, mark, session_date in assigned.values_list("enrollment_id", "mark", "session__date"):
        if eid in stopped:
            continue
        if mark != "ABSENT":
            stopped.add(eid)
        else:
            count, latest = streaks.get(eid, (0, session_date))
            streaks[eid] = count + 1, latest
    return [(eid, "absence_streak", count, f"{latest} 기준 배정 차시 {count}회 연속 결석")
            for eid, (count, latest) in streaks.items() if count >= threshold]


def homework_signals(tenant, roster, today, rules):
    submitted = Submission.objects.filter(tenant=tenant, enrollment_id=OuterRef("enrollment_id"),
        target_type="homework", target_id=OuterRef("homework_id"))
    assigned = HomeworkAssignment.objects.filter(
        tenant=tenant, enrollment_id__in=roster, homework__tenant=tenant, homework__homework_type="regular",
        homework__session_id=F("session_id"), session__lecture_id=F("enrollment__lecture_id"),
    ).exclude(homework__meta__removed_from_session_at__isnull=False).annotate(submitted=Exists(submitted))
    scores = HomeworkScore.objects.filter(attempt_index=1, enrollment_id__in=roster, homework__tenant=tenant,
        session_id=F("homework__session_id"), session__lecture_id=F("enrollment__lecture_id"))
    records = {(eid, hid): (score, maximum, (meta or {}).get("status"), approved)
               for eid, hid, score, maximum, meta, approved in scores.values_list(
                   "enrollment_id","homework_id","score","max_score","meta","teacher_approved")}
    missing = defaultdict(list)
    history = defaultdict(list)
    for row in assigned.values("enrollment_id","homework_id","homework__title","homework__meta",
            "homework__grading_mode","session__date","session__order","submitted"):
        eid, hid, title = row["enrollment_id"], row["homework_id"], row["homework__title"]
        score, maximum, status, approved = records.get((eid, hid), (None, None, None, False))
        try:
            due = date.fromisoformat((row["homework__meta"] or {}).get("due_date") or "")
        except (TypeError, ValueError):
            due = None
        if due and due < today and not row["submitted"] and not approved and (score is None or status == "NOT_SUBMITTED") and status != "VIDEO_MAKEUP":
            missing[eid].append((due, title))
        if row["session__date"] and row["session__date"] <= today and row["homework__grading_mode"] == "SCORE":
            rate = score_rate(score, maximum) if score is not None and maximum and maximum > 0 and status not in ("NOT_SUBMITTED","VIDEO_MAKEUP") else None
            history[eid].append((row["session__date"], row["session__order"], hid, title, rate))
    signals = []
    if rules["homework_missing"]["enabled"]:
        for eid, entries in missing.items():
            if len(entries) >= rules["homework_missing"]["threshold"]:
                due, title = max(entries)
                signals.append((eid, "homework_missing", len(entries), f"기한 경과 미제출 {len(entries)}건 · 최근 {due} {title}"))
    if rules["homework_drop"]["enabled"]:
        signals.extend(deltas(history, rules, "homework_drop"))
    return signals


def deltas(history, rules, drop, rise=None):
    signals = []
    for eid, entries in history.items():
        if len(entries) < 2:
            continue
        prev, current = sorted(entries)[-2:]
        if prev[-1] is None or current[-1] is None:
            continue
        diff = current[-1] - prev[-1]
        key = rise if diff > 0 else drop
        if key and rules[key]["enabled"] and abs(diff) >= rules[key]["threshold"]:
            signals.append((eid, key, round(abs(diff), 2),
                            f"{prev[-2]} {prev[-1]:.1f}% → {current[-2]} {current[-1]:.1f}% ({diff:+.1f}%p)"))
    return signals


def exam_signals(tenant, roster, today, rules):
    assignments = ExamEnrollment.objects.filter(enrollment_id__in=roster, exam__tenant=tenant,
                                                 exam__exam_type="regular", exam__is_active=True)
    pairs = list(assignments.values_list("enrollment_id", "exam_id", "exam__title", "exam__max_score"))
    maxima = {exam: float(maximum or 100) for _, exam, _, maximum in pairs}
    if not pairs:
        return []
    dates = defaultdict(set)
    for exam, lecture, day, order in Session.objects.filter(lecture__tenant=tenant, exams__in=maxima).values_list(
            "exams__id", "lecture_id", "date", "order"):
        dates[exam, lecture].add((day, order))
    results = latest_results_for_targets_per_enrollment(target_type="exam", target_ids=maxima).filter(
        enrollment__tenant=tenant, enrollment_id__in=roster).select_related("attempt")
    scored = _load_scores(results=list(results), exam_maxima=maxima, tenant=tenant)
    history = defaultdict(list)
    cohorts = defaultdict(dict)
    for eid, exam, title, _ in pairs:
        sessions = dates[exam, roster[eid].lecture_id]
        if len(sessions) != 1:
            continue
        day, order = next(iter(sessions))
        if not day or day > today:
            continue
        entry = scored.get((exam, eid))
        rate = (score_rate(entry.initial.total_score, entry.initial.max_score) if entry and entry.status == "DONE"
                and entry.initial.max_score and entry.initial.max_score > 0 else None)
        history[eid].append((day, order, exam, title, rate))
        if rate is not None:
            cohorts[roster[eid].lecture_id, exam][eid] = entry
    signals = deltas(history, rules, "exam_drop", "exam_rise")
    from apps.domains.students.models import ClassGroupLecture
    references = dict(ClassGroupLecture.objects.filter(
        group__tenant=tenant, lecture__tenant=tenant, lecture_id__in={lecture for lecture, _ in cohorts},
    ).values_list("lecture_id", "top_reference_percent"))
    benchmarks = {(lecture, exam): top_reference(rows, references[lecture])["top_reference_average"]
                  for (lecture, exam), rows in cohorts.items() if lecture in references and len(rows) > 1}
    for eid, entries in history.items():
        ordered = sorted(entries)
        _, _, exam, title, rate = ordered[-1]
        if rate is None:
            continue
        recent = [row[-1] for row in ordered[:-1] if row[-1] is not None][-3:]
        if len(recent) == 3 and rules["recent_exam_drop"]["enabled"]:
            baseline = fmean(recent)
            if baseline - rate >= rules["recent_exam_drop"]["threshold"]:
                signals.append((eid, "recent_exam_drop", round(baseline-rate, 2),
                                f"직전 3회 평균 {baseline:.1f}% → {title} {rate:.1f}%"))
        lecture = roster[eid].lecture_id
        reference = benchmarks.get((lecture, exam))
        if reference is not None and rules["top_reference_gap"]["enabled"] and reference-rate >= rules["top_reference_gap"]["threshold"]:
            signals.append((eid, "top_reference_gap", round(reference-rate, 2),
                            f"{title} {rate:.1f}% · 강의 상위 {references[lecture]}% 평균 {reference:.1f}%"))
    return signals


def video_signals(tenant, roster, today, threshold):
    sessions = SessionEnrollment.objects.filter(tenant=tenant, enrollment_id__in=roster,
        session__lecture_id=F("enrollment__lecture_id"), session__date__lt=today)
    assigned = defaultdict(list)
    for sid, eid in sessions.values_list("session_id", "enrollment_id"):
        assigned[sid].append(roster[eid])
    videos = list(Video.objects.filter(tenant=tenant, session_id__in=assigned, status=Video.Status.READY,
        source_type=Video.SourceType.UPLOADED, visibility=Video.Visibility.ENROLLED).exclude(hls_path="").order_by("id"))
    if not videos:
        return []
    ids = [v.pk for v in videos]
    permissions = defaultdict(dict)
    progress = defaultdict(dict)
    marks = defaultdict(dict)
    for obj in VideoAccess.objects.filter(video_id__in=ids, enrollment_id__in=roster):
        permissions[obj.video_id][obj.enrollment_id] = obj
    for obj in VideoProgress.objects.filter(video_id__in=ids, enrollment_id__in=roster):
        progress[obj.video_id][obj.enrollment_id] = obj
    for sid, eid, mark in Attendance.objects.filter(tenant=tenant, session_id__in=assigned,
            enrollment_id__in=roster).values_list("session_id", "enrollment_id", "status"):
        marks[sid][eid] = mark
    low = defaultdict(list)
    for video in videos:
        modes = resolve_access_modes_prefetched(video=video, enrollments=assigned[video.session_id],
            progresses_by_enrollment_id=progress[video.pk], access_by_enrollment_id=permissions[video.pk],
            attendance_status_by_enrollment_id=marks[video.session_id])
        for eid, mode in modes.items():
            if mode != AccessMode.PROCTORED_CLASS:
                continue
            state = progress[video.pk].get(eid)
            percent = normalize_video_progress(state.progress if state else 0) * 100
            if percent < threshold:
                low[eid].append((percent, video.title))
    signals = []
    for eid, entries in low.items():
        percent, title = min(entries)
        signals.append((eid, "video_watch", round(percent, 2),
                        f"시청 기준 미달 영상 {len(entries)}개 · {title} {percent:.1f}%"))
    return signals


def signals_for_lectures(tenant, lectures, today, rules):
    active = TenantMembership.objects.filter(tenant=tenant, role="student", user_id=OuterRef("student__user_id"),
                                             is_active=True, user__is_active=True)
    rows = Enrollment.objects.filter(tenant=tenant, lecture_id__in=lectures, status="ACTIVE", student__tenant=tenant,
        student__deleted_at__isnull=True, student__is_managed=True).filter(Exists(active)).select_related("student")
    roster = {e.pk: e for e in rows}
    if not roster:
        return []
    signals = []
    if rules["absence_streak"]["enabled"]:
        signals.extend(attendance_signals(tenant, roster, today, rules["absence_streak"]["threshold"]))
    if rules["homework_missing"]["enabled"] or rules["homework_drop"]["enabled"]:
        signals.extend(homework_signals(tenant, roster, today, rules))
    if any(rules[key]["enabled"] for key in ("exam_rise", "exam_drop", "recent_exam_drop", "top_reference_gap")):
        signals.extend(exam_signals(tenant, roster, today, rules))
    if rules["video_watch"]["enabled"]:
        signals.extend(video_signals(tenant, roster, today, rules["video_watch"]["threshold"]))
    return sorted([{"student_id": roster[eid].student_id, "student_name": roster[eid].student.name,
                    "lecture_id": roster[eid].lecture_id, "lecture_title": lectures[roster[eid].lecture_id],
                    "rule": rule, "value": value, "detail": detail}
                   for eid, rule, value, detail in signals], key=lambda s: (s["lecture_title"],s["student_name"],s["rule"],s["student_id"]))
