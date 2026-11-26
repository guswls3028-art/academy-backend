"""Resolve printed numbers against the currently authorized, canonical question sheet."""

from dataclasses import dataclass

from apps.domains.results.services.answer_matching import format_answer_for_display
from apps.support.results.range_wrong_note_dependencies import range_question_records, range_source_records
from apps.support.results.wrong_note_dependencies import question_image_key, explanation_image_key


class RangeSelectionError(ValueError):
    pass


@dataclass
class PrintSource:
    info: dict
    items: dict
    enrolled: set


class WrongNoteRange:
    def __init__(self, *, tenant_id, lecture_id, first, last, enrollment_id=None, selection=None):
        enrollments, records = range_source_records(
            tenant_id=tenant_id, lecture_id=lecture_id, first=first, last=last,
            enrollment_id=enrollment_id, selection=selection,
        )
        self.students = {row.pk: row for row in enrollments}
        self.sources = {}
        questions, keys = range_question_records(
            tenant_id=tenant_id, exams=[exam for _, _, _, _, exam, _ in records if exam],
        )
        for kind, source_id, title, order, exam, enrolled in sorted(
            records, key=lambda row: (row[3], row[0], row[2], row[1]),
        ):
            owner = exam.effective_structure_exam_id if exam else None
            usable = exam is not None and (
                exam.segmentation_status == "ready"
                or (kind == "exam" and exam.segmentation_status == "none")
            )
            items = {}
            numbers = []
            answers = keys.get(owner) or {}
            for q in questions.get(owner, []):
                numbers.append(q.number)
                answer = format_answer_for_display(answers.get(str(q.pk), ""))
                image_key = question_image_key(question=q, tenant_id=tenant_id)
                image_name = str(q.image.name or "") if q.image else ""
                if not image_name.startswith(("exams/questions/", f"tenants/{tenant_id}/")) or ".." in image_name.split("/"):
                    image_name = ""
                if not usable or not answer or not (image_key or image_name):
                    continue
                explanation = getattr(q, "explanation", None)
                text = explanation.text if explanation else ""
                solution_key = explanation_image_key(question=q, tenant_id=tenant_id)
                items[q.number] = {
                    "source_type": kind, "source_id": source_id, "exam_id": exam.pk,
                    "exam_title": title, "session_order": order, "session_title": "",
                    "question_number": q.number, "question_id": q.pk,
                    "answer_type": q.question_kind or "", "correct_answer": answer,
                    "attempt_created_at": max(q.updated_at, explanation.updated_at) if explanation else q.updated_at,
                    "has_question_image": True, "has_teacher_explanation": bool(text or solution_key),
                    "extra": {"explanation_text": text}, "_question_image_key": image_key,
                    "_question_image_name": image_name, "_explanation_image_key": solution_key,
                    "_required_problem_image": True,
                }
            self.sources[(kind, source_id)] = PrintSource({
                "type": kind, "id": source_id, "title": title, "session_order": order,
                "question_numbers": numbers, "unavailable_numbers": [n for n in numbers if n not in items],
                "enrollment_ids": sorted(enrolled), "ready": bool(items),
            }, items, enrolled)

    def select(self, enrollment_id, sources):
        if enrollment_id not in self.students:
            raise RangeSelectionError("현재 강의의 수강생을 다시 선택해 주세요.")
        items = []
        seen = set()
        for entry in sources:
            key = (entry["type"], entry["id"])
            source = self.sources.get(key)
            if not source or enrollment_id not in source.enrolled:
                raise RangeSelectionError("선택 범위에서 학생에게 배정된 자료만 출력할 수 있습니다.")
            if key in seen:
                raise RangeSelectionError("같은 학생의 자료가 중복되었습니다.")
            seen.add(key)
            numbers = entry["numbers"]
            if len(numbers) != len(set(numbers)):
                raise RangeSelectionError("한 자료에 같은 문항 번호를 두 번 입력할 수 없습니다.")
            for number in numbers:
                if number not in source.items:
                    raise RangeSelectionError(f"{source.info['title']} {number}번의 문항 번호와 승인된 문제·정답을 확인해 주세요.")
                items.append({**source.items[number], "enrollment_id": enrollment_id})
        if not 1 <= len(items) <= 100:
            raise RangeSelectionError("학생 한 명당 1~100문항을 선택해 주세요.")
        return items
