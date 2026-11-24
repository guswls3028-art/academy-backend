"""Validate the homework results actually used in a teacher's session report."""

from collections import defaultdict
from fnmatch import fnmatchcase
import json
import re

from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import HomeworkScore
from apps.support.results.session_scores_dependencies import superseded_homework_missing_keys
from apps.support.submissions.dependencies import staff_homework_file_targets
from apps.support.submissions.homework_acceptance import homework_accepted_keys


TOKEN = re.compile(r"#\{([^{}\n]+)\}")
RESULT_SLOT = re.compile(r"과제([1-9]\d?)(?:결과)?\Z")
TITLE_SLOT = re.compile(r"과제([1-9]\d?)명\Z")
AGGREGATES = {"과제목록", "시험성적", "선생님메모", "전체요약", "숙제완성도"}
DETAILS = {"시험성적", "선생님메모"}
STATE_KEY = "_homework_notice_states"


def _literal(text):
    # Escape author text and titles before inserting them in a glob.
    return text.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def _legacy_claim(body, rendered, slot, titles, assigned):
    """Older clients send only the rendered body. Match generated slots or titled cards."""
    def result_pattern(label):
        parts = []
        offset = 0
        checked = False
        for token in TOKEN.finditer(body):
            parts.append(_literal(body[offset:token.start()]))
            name = token[1]
            offset = token.end()
            result = RESULT_SLOT.fullmatch(name)
            title_slot = TITLE_SLOT.fullmatch(name)
            if result and int(result[1]) == slot:
                parts.append(label)
                checked = True
            elif title_slot and int(title_slot[1]) in titles:
                parts.append(_literal(titles[int(title_slot[1])]))
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

    if not any(label in rendered and result_pattern(label) for label in ("미제출", "영상보강")):
        return False
    # Adjacent substitutions in an old custom template may have no delimiter. Do not
    # attribute another field's wording to this assignment if a current result also fits.
    return not any(result_pattern(value) for value in (
        "점수 미입력", "검사 대기", "통과*", "미통과*", "[0-9]*",
    ))


def grade_homework_notice_changed(*, tenant, session, enrollments, body, contexts, lock):
    """Read the selected roster and compare the same data again inside final outbox admission."""
    names = TOKEN.findall(body)
    numbered = {int(slot[1]) for name in names if (slot := RESULT_SLOT.fullmatch(name))}
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
        if isinstance(rendered, str) and ("미제출" in rendered or "영상보강" in rendered):
            legacy[student_id] = rendered
    if not (snapshots or legacy) or not (all_slots or numbered):
        return False

    homeworks = staff_homework_file_targets(tenant=tenant).filter(session=session).order_by("display_order", "created_at", "id")
    ordered = list(homeworks.values_list("pk", "title"))
    by_id = {hid: n for n, (hid, _) in enumerate(ordered, 1)}
    titles = {n: title for n, (_, title) in enumerate(ordered, 1)}
    ids = [hid for hid, n in by_id.items() if all_slots or n in numbered]
    targets = {eid: sid for eid, sid in enrollments.items() if sid in snapshots or sid in legacy}
    assignments = HomeworkAssignment.objects.filter(tenant=tenant, session=session, homework_id__in=ids,
        enrollment_id__in=targets, homework__tenant=tenant, homework__session=session).order_by("pk")
    if lock:
        assignments = assignments.select_for_update(of=("self",))
    pairs = list(assignments.values_list("enrollment_id", "homework_id"))
    accepted = homework_accepted_keys(tenant=tenant, enrollment_ids=targets, homework_ids=ids)
    scores = {(s.enrollment_id, s.homework_id): s for s in HomeworkScore.objects.filter(session=session,
        enrollment__tenant=tenant, enrollment_id__in=targets, homework_id__in=ids, attempt_index=1)
        .only("enrollment_id","homework_id","session_id","score","meta","reviewed_submission_revision")}
    cleared = superseded_homework_missing_keys(tenant=tenant, scores=scores.values(), accepted_keys=accepted)
    states = defaultdict(dict)
    for eid, hid in pairs:
        score = scores.get((eid, hid))
        if (hid, session.pk, eid) in accepted:
            state = "accepted"
        elif (score and score.score is None and isinstance(score.meta, dict)
              and score.meta.get("status") == HomeworkScore.MetaStatus.NOT_SUBMITTED and (eid, hid) not in cleared):
            state = "missing"
        else:
            state = "other"
        states[targets[eid]][by_id[hid]] = [hid, state]

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
            if state != "missing" and _legacy_claim(body, rendered, slot, titles, sorted(visible)):
                return True
    return False
