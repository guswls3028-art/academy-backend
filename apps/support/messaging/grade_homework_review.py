"""Validate the homework results actually used in a teacher's session report."""

from collections import defaultdict
from fnmatch import fnmatchcase
import json
import re
from uuid import UUID

from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import HomeworkScore
from apps.support.results.session_scores_dependencies import superseded_homework_missing_keys
from apps.support.submissions.dependencies import staff_homework_file_targets
from apps.support.submissions.homework_acceptance_queries import homework_accepted_keys


TOKEN = re.compile(r"#\{([^{}\n]+)\}")
RESULT_SLOT = re.compile(r"과제([1-9][0-9]*)(?:결과)?\Z")
ASSESSMENT_SLOT = re.compile(r"#\{(?:과제|시험)[1-9][0-9]*(?:명|만점|결과|최고점)?\}")
EMPTY_GRADE_LINE = re.compile(r"(?:[^\n]*:\s*-?\s*/\s*-?|\s*:|\s*-\s*:\s*-)?\Z")
AGGREGATES = {"과제목록", "시험성적", "선생님메모", "전체요약", "숙제완성도"}
DETAILS = {"시험성적", "선생님메모"}
STATE_KEY = "_homework_notice_states"
OUTBOX_GUARD_KEY = "grade_homework_review"
OUTBOX_REFERENCE_PREFIX = "grade_homework_outbox:"
HOMEWORK_NOTICE_TRIGGERS = {"manual_send", "assignment_not_submitted"}


def _literal(text):
    # Escape author text and titles before inserting them in a glob.
    return text.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def _legacy_claim(body, rendered, slot, titles, assigned):
    """Older clients send only the rendered body. Match generated slots or titled cards."""
    title_fields = {f"과제{number}명": title for number, title in titles.items()}
    fields = {f"과제{slot}", f"과제{slot}결과", "과제목록", *DETAILS}
    source = []
    optional_row = False
    for line in body.strip().splitlines():
        if not line.strip():
            continue
        # The previous score composer removes empty or unused template rows after substitution.
        if not fields.intersection(TOKEN.findall(line)) and ASSESSMENT_SLOT.search(line):
            if EMPTY_GRADE_LINE.fullmatch(ASSESSMENT_SLOT.sub("", line).strip()):
                optional_row = True
                continue
        if source:
            source.append("\n")
        if optional_row:
            source.append("#{_optional_grade_row}")
            optional_row = False
        source.append(line)
    if optional_row:
        source.append("#{_optional_grade_row}")
    body = "".join(source)
    rendered = "\n".join(line for line in rendered.strip().splitlines() if line.strip())

    def result_pattern(label):
        parts = []
        offset = 0
        checked = False
        for token in TOKEN.finditer(body):
            parts.append(_literal(body[offset:token.start()]))
            name = token[1]
            offset = token.end()
            if name in (f"과제{slot}", f"과제{slot}결과"):
                parts.append(label)
                checked = True
            elif name in title_fields:
                parts.append(_literal(title_fields[name]))
            elif name == "과제목록" or name in DETAILS:
                # Include the other assigned titles in display order. Two tasks may share a title.
                entries = []
                for number in assigned:
                    title = _literal(titles[number])
                    value = label if number == slot else "*"
                    entries.append(f"- {title}: {value}" if name == "과제목록" else f"┌ {title}\n└ {value}")
                lines = "\n".join(entries)
                parts.append(lines if name == "과제목록" else "*[[]과제]\n" + lines + "*")
                checked = True
            else:
                parts.append("*")
        parts.append(_literal(body[offset:]))
        return checked and fnmatchcase(rendered.strip(), "".join(parts).strip())

    if not result_pattern("미제출"):
        return False
    # Adjacent substitutions in an old custom template may have no delimiter. Do not
    # attribute another field's wording to this assignment if a current result also fits.
    return not any(result_pattern(value) for value in (
        "점수 미입력", "검사 대기", "통과*", "미통과*", "[0-9]*",
    ))


def _assignment_states(tenant, session_id, assignments):
    enrollments = {enrollment_id for _, enrollment_id, _ in assignments}
    homeworks = {homework_id for _, _, homework_id in assignments}
    accepted = homework_accepted_keys(tenant=tenant, enrollment_ids=enrollments, homework_ids=homeworks)
    scores = {(row.enrollment_id, row.homework_id): row for row in HomeworkScore.objects.filter(
        session_id=session_id, enrollment__tenant=tenant, enrollment_id__in=enrollments,
        homework_id__in=homeworks, attempt_index=1,
    ).only("enrollment_id", "homework_id", "session_id", "score", "meta", "reviewed_submission_revision")}
    cleared = superseded_homework_missing_keys(tenant=tenant, scores=scores.values(), accepted_keys=accepted)
    states = {}
    for assignment_id, enrollment_id, homework_id in assignments:
        pair = (enrollment_id, homework_id)
        score = scores.get(pair)
        if (homework_id, session_id, enrollment_id) in accepted:
            states[assignment_id] = "accepted"
        elif (score and score.score is None and isinstance(score.meta, dict)
              and score.meta.get("status") == HomeworkScore.MetaStatus.NOT_SUBMITTED and pair not in cleared):
            states[assignment_id] = "missing"
        else:
            states[assignment_id] = "other"
    return states


def grade_homework_notice_changed(*, tenant, session, enrollments, body, contexts, lock, missing_assignments=None):
    """Read the selected roster and compare the same data again inside final outbox admission."""
    names = TOKEN.findall(body)
    numbered = {slot[1] for name in names if (slot := RESULT_SLOT.fullmatch(name))}
    all_slots = bool(AGGREGATES.intersection(names))
    snapshots = {}
    legacy = {}
    for student_id in enrollments.values():
        context = contexts.get(str(student_id), contexts.get(student_id))
        if not isinstance(context, dict):
            continue
        if STATE_KEY in context:
            snapshots[student_id] = context.pop(STATE_KEY)
        rendered = context.get("_body_subst")
        if isinstance(rendered, str) and "미제출" in rendered:
            legacy[student_id] = rendered
    if not (snapshots or legacy) or not (all_slots or numbered):
        return False

    homeworks = staff_homework_file_targets(tenant=tenant).filter(session=session).order_by("display_order", "created_at", "id")
    ordered = list(homeworks.values_list("pk", "title"))
    by_id = {hid: n for n, (hid, _) in enumerate(ordered, 1)}
    titles = {n: title for n, (_, title) in enumerate(ordered, 1)}
    ids = [hid for hid, n in by_id.items() if all_slots or str(n) in numbered]
    targets = {eid: sid for eid, sid in enrollments.items() if sid in snapshots or sid in legacy}
    assignments = HomeworkAssignment.objects.filter(tenant=tenant, session=session, homework_id__in=ids,
        enrollment_id__in=targets, homework__tenant=tenant, homework__session=session).order_by("pk")
    if lock:
        assignments = assignments.select_for_update(of=("self",))
    rows = list(assignments.values_list("pk", "enrollment_id", "homework_id"))
    current = _assignment_states(tenant, session.pk, rows)
    states = defaultdict(dict)
    assignment_by_student_slot = {}
    for assignment_id, eid, hid in rows:
        student_id, slot = targets[eid], by_id[hid]
        states[student_id][slot] = [hid, current[assignment_id]]
        assignment_by_student_slot[(student_id, slot)] = assignment_id

    for student_id, raw in snapshots.items():
        # Compact JSON from the grade board has three bounded scalars per referenced slot.
        if not isinstance(raw, str) or len(raw) > 64 * len(ids) + 2:
            return True
        try:
            rows = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            return True
        if not isinstance(rows, list) or not rows:
            return True
        seen = set()
        for row in rows:
            if (not isinstance(row, list) or len(row) != 3 or type(row[0]) is not int
                or type(row[1]) is not int or row[0] in seen or states[student_id].get(row[0]) != row[1:]):
                return True
            seen.add(row[0])
        # A student may have assigned but hidden work after an absence. Only visible results
        # are in the client's snapshot. Check additional declared-missing/accepted results too.
        if any(number not in seen and state != "other" for number, (_, state) in states[student_id].items()):
            return True
    for student_id, rendered in legacy.items():
        visible = states[student_id]
        for slot, (_, state) in visible.items():
            if (state != "missing" or missing_assignments is not None) and _legacy_claim(
                body, rendered, slot, titles, sorted(visible),
            ):
                if state == "missing":
                    missing_assignments.setdefault(student_id, []).append(assignment_by_student_slot[(student_id, slot)])
                    continue
                return True
    return False


def _missing_grade_owners(tenant, session_id, assignment_ids, lock):
    homeworks = staff_homework_file_targets(tenant=tenant).filter(session_id=session_id)
    assignments = HomeworkAssignment.objects.filter(
        pk__in=assignment_ids, tenant=tenant, session_id=session_id, homework__in=homeworks,
        enrollment__tenant=tenant, enrollment__status="ACTIVE", enrollment__student__tenant=tenant,
        enrollment__student__deleted_at__isnull=True,
    ).order_by("pk")
    if lock:
        assignments = assignments.select_for_update(of=("self",))
    rows = list(assignments.values_list("pk", "enrollment_id", "homework_id", "enrollment__student_id"))
    current = _assignment_states(tenant, session_id, [(pk, eid, hid) for pk, eid, hid, _ in rows])
    return {pk: student_id for pk, _, _, student_id in rows if current.get(pk) == "missing"}


def stale_grade_homework_outboxes(tenants, notifications, *, lock=True):
    """Recheck exact refs under the same assignment locks as file submissions."""
    groups = defaultdict(list)
    stale = set()
    for notification in notifications:
        guard = notification.grade_homework_review
        if notification.trigger not in HOMEWORK_NOTICE_TRIGGERS or guard is None:
            continue
        if not isinstance(guard, dict):
            stale.add(notification.pk)
            continue
        session_id, student_id, assignment_ids = (guard.get(key) for key in ("session_id", "student_id", "assignment_ids"))
        if (type(session_id) is not int or type(student_id) is not int or not isinstance(assignment_ids, list)
                or not assignment_ids or any(type(key) is not int for key in assignment_ids)):
            stale.add(notification.pk)
            continue
        inbox_homework_id = None
        if notification.trigger == "assignment_not_submitted":
            inbox_homework_id = guard.get("homework_id")
            if type(inbox_homework_id) is not int or inbox_homework_id <= 0:
                stale.add(notification.pk)
                continue
        group = (notification.tenant_id, session_id, notification.trigger, inbox_homework_id)
        groups[group].append((notification.pk, student_id, assignment_ids))

    for (tenant_id, session_id, _, inbox_homework_id), entries in sorted(groups.items()):
        tenant = tenants[tenant_id]
        if inbox_homework_id:
            from apps.support.messaging.assignment_not_submitted_dependencies import missing_homework_assignments, missing_homework_target

            homework = missing_homework_target(tenant, inbox_homework_id)
            eligible = missing_homework_assignments(
                tenant=tenant, homework=homework, student_ids={sid for _, sid, _ in entries}, lock=lock,
            ) if homework and homework.session_id == session_id else []
            owners = {row.pk: row.enrollment.student_id for row in eligible}
        else:
            owners = _missing_grade_owners(tenant, session_id, {pk for _, _, ids in entries for pk in ids}, lock)
        stale.update(
            notification_id for notification_id, student_id, ids in entries
            if any(owners.get(pk) != student_id for pk in ids)
        )
    return stale


def grade_homework_delivery_is_stale(*, business_tenant_id, business_key, reference, trigger="manual_send"):
    """Recheck after queue delay using the exact durable outbox and business tenant."""
    from apps.domains.messaging.models import ScheduledNotification

    try:
        dispatch_key = UUID(hex=reference.removeprefix(OUTBOX_REFERENCE_PREFIX))
    except ValueError:
        return True
    outbox = ScheduledNotification.objects.select_related("tenant").filter(
        tenant_id=business_tenant_id, dispatch_key=dispatch_key, trigger=trigger,
        business_idempotency_key=business_key, grade_homework_review__isnull=False,
    ).exclude(status__in=(ScheduledNotification.Status.CANCELLED, ScheduledNotification.Status.FAILED)).first()
    return outbox is None or outbox.pk in stale_grade_homework_outboxes(
        {business_tenant_id: outbox.tenant}, [outbox], lock=False,
    )
