"""Exercise the candidate's public API and verify the two students' actual ZIP members."""
import io
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import fitz
import requests
from django.conf import settings
from django.core.management.base import CommandError
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from apps.core.models import OpsAuditLog
from apps.core.services.development_qa_queue_ack import require_acks
from apps.domains.ai.models import AIJobModel
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import Result, WrongNotePDF
from apps.domains.results.services.wrong_note_batch_archive import batch_archive_key, student_filename


def require(condition, detail):
    if not condition:
        raise CommandError(detail)


def expected_answer(number):
    return str((number - 1) % 5 + 1)


def synthetic_paper(count):
    """A wholly synthetic two-column paper, with independently numbered solutions."""
    font = Path(settings.BASE_DIR) / "domains/assets/omr/renderer/fonts/NotoSansKR-Regular.ttf"
    pdfmetrics.registerFont(TTFont("QaExam", str(font)))
    buffer = io.BytesIO()
    pdf = Canvas(buffer, pagesize=(595, 842))

    def line(x, y, text, size=11):
        pdf.setFont("QaExam", size)
        pdf.drawString(x, 842 - y, text)

    choices = "①②③④⑤"
    for first in range(1, count + 1, 5):
        line(46, 56, "검증용 평가 · 수리 영역", 15)
        for slot, n in enumerate(range(first, min(first + 5, count + 1))):
            column, row = (0, slot) if slot < 3 else (1, slot - 3)
            x, y = 48 + column * 255, 110 + row * 220
            line(x, y, f"{n}. 다음 중 {n} + 1 의 값은 무엇입니까?", 9)
            pdf.roundRect(x, 842 - y - 66, 185, 38, 4, stroke=1)
            line(x + 8, y + 51, f"{n} + 1 = □")
            line(x, y + 101, "   ".join(f"{choice} {n + 1 + ((i - (n - 1)) % 5)}" for i, choice in enumerate(choices)), 9)
        pdf.showPage()
    for first in range(1, count + 1, 8):
        line(45, 52, "빠른 정답" if first == 1 else "선생님 풀이", 16)
        for slot, n in enumerate(range(first, min(first + 8, count + 1))):
            column, row = divmod(slot, 4)
            x, y = 48 + column * 255, 106 + row * 148
            line(x, y, f"{n}. [답] {choices[(n - 1) % 5]}")
            line(x, y + 25, "[풀이]")
            line(x, y + 45, f"확인용 풀이 {n}번")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


class UncertainWrite(CommandError):
    """A disconnected HTTP writer can still commit after the client returns."""


def api(session, method, path, expected=200, **kwargs):
    try:
        response = session.request(method, f"http://127.0.0.1:8000/api/v1/{path}",
                                   timeout=(5, 60), allow_redirects=False, **kwargs)
    except requests.RequestException:
        if method != "GET":
            raise UncertainWrite(f"Unconfirmed development write to {path}") from None
        raise CommandError(f"Development read failed for {path}") from None
    require(response.status_code == expected, f"{method} {path} returned {response.status_code}")
    return response.json()


def poll(read, complete, failed, label, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        require(not failed(value), f"{label} failed")
        if complete(value):
            return value
        time.sleep(2)
    raise CommandError(f"{label} did not complete within {timeout}s")


def verify_pdf(data, name, expected_numbers):
    problem_numbers, answer_pairs = [], []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        require(pdf.metadata.get("title") == f"{name} 오답노트", "PDF student identity differs")
        for page in pdf:
            text = page.get_text()
            numbers = [int(n) for n in re.findall(r"(?m)^\s*(\d+)번\s*$", text)]
            if not numbers:
                continue
            answer = re.search(r"(?m)^정답\s+([1-5])\s*$", text)
            if answer:
                require(len(numbers) == 1, "Answer page has ambiguous question number")
                answer_pairs.append((numbers[0], answer[1]))
            else:
                require(len(page.get_images()) >= len(numbers), "Selected problem image is missing")
                problem_numbers.extend(numbers)
    require(sorted(problem_numbers) == sorted(expected_numbers), "PDF problem selection differs")
    require(sorted(answer_pairs) == sorted((n, expected_answer(n)) for n in expected_numbers),
            "PDF answer key differs")


def segment_and_approve(http, exam, count):
    uploaded = api(http, "POST", "exams/pdf-extract/", 202, data={"exam_id": exam.pk},
                   files={"file": ("synthetic-science.pdf", synthetic_paper(count), "application/pdf")})
    require(uploaded.get("processing_started") and uploaded.get("job_id"), "Segmentation was not queued")
    review_path = f"exams/{exam.pk}/segmentation-review/"
    review = poll(lambda: api(http, "GET", review_path), lambda r: r["status"] == "review_required",
                  lambda r: r["status"] == "failed", "Question segmentation", 420)
    items = review["items"]
    require(len(items) == count and {row["number"] for row in items} == set(range(1, count + 1))
            and review["paired_source_status"] == "complete" and review["answer_source_requested"]
            and all(row["problem_image_url"] and row["answer"] == expected_answer(row["number"]) for row in items),
            "Synthetic PDF question or paired answer extraction differs")
    approved = api(http, "POST", review_path + "approve/", json={
        "items": [{k: row[k] for k in ("id", "number", "answer", "included")} for row in items],
    })
    require(approved["status"] == "ready" and api(http, "GET", review_path)["status"] == "ready",
            "Approved review did not persist")
    questions = list(ExamQuestion.objects.filter(sheet__exam=exam).values_list("pk", "number"))
    answers = AnswerKey.objects.get(exam=exam).answers
    require(len(questions) == count and answers == {str(pk): expected_answer(n) for pk, n in questions},
            "Approved canonical answers differ")
    return uploaded["job_id"]


def run(tenant, password):
    lecture = Lecture.objects.filter(tenant=tenant).order_by("pk").first()
    lessons = list(Session.objects.filter(lecture=lecture, session_type="REGULAR").order_by("regular_order"))
    enrollments = list(Enrollment.objects.filter(tenant=tenant, lecture=lecture).select_related("student").order_by("pk"))
    require([s.regular_order for s in lessons] == [1, 2] and len(enrollments) == 2, "Unexpected QA roster")
    exam = Exam.objects.create(tenant=tenant, title="합성 과학", exam_type="regular", grading_mode="choice",
                               choice_question_count=25, max_score=100)
    exam.sessions.add(lessons[0])
    ExamEnrollment.objects.bulk_create([ExamEnrollment(exam=exam, enrollment=e) for e in enrollments])
    with requests.Session() as http:
        http.trust_env = False
        http.headers["X-Tenant-Code"] = tenant.code
        token = api(http, "POST", "token/", json={"username": "ymath-qa-teacher", "password": password})
        http.headers["Authorization"] = f"Bearer {token['access']}"
        exam_job = segment_and_approve(http, exam, 25)
        homework = Homework.objects.create(tenant=tenant, session=lessons[1], title="합성 실험 과제")
        api(http, "POST", f"homeworks/{homework.pk}/source-exam/")
        homework.refresh_from_db()
        hw_job = segment_and_approve(http, homework.source_exam, 5)
        for enrollment in enrollments:
            HomeworkAssignment.objects.create(tenant=tenant, session=lessons[1], homework=homework, enrollment=enrollment)
        scope = {"lecture_id": lecture.pk, "from_session_order": 1, "to_session_order": 2}
        root = "results/wrong-notes/"
        catalog = api(http, "GET", root + "range/", params=scope)
        materials = {(row["type"], row["id"]): row for row in catalog["sources"]}
        for identity, count in [(("exam", exam.pk), 25), (("homework", homework.pk), 5)]:
            row = materials[identity]
            require(row["ready"] and sorted(row["question_numbers"]) == list(range(1, count + 1))
                    and not row["unavailable_numbers"]
                    and set(row["enrollment_ids"]) == {e.pk for e in enrollments}, "Range source is incomplete")
        numbers = [(25, 4), (7, 2)]
        selection = [{"enrollment_id": e.pk, "sources": [
            {"type": "exam", "id": exam.pk, "numbers": [a]}, {"type": "homework", "id": homework.pk, "numbers": [b]},
        ]} for e, (a, b) in zip(enrollments, numbers)]
        payload = {**scope, "students": selection}
        def grading_state():
            return (
                list(Result.objects.filter(enrollment__in=enrollments).order_by("pk").values()),
                list(HomeworkScore.objects.filter(enrollment__in=enrollments).order_by("pk").values()),
                list(AnswerKey.objects.filter(exam_id__in=[exam.pk, homework.source_exam_id]).order_by("pk").values()),
            )
        before = grading_state()
        batch = api(http, "POST", root + "batches/", 202, json=payload)
        duplicate = api(http, "POST", root + "batches/", 202, json=payload)
        require(duplicate["id"] == batch["id"], "Repeating an identical print request created another batch")
        path = root + f"batches/{batch['id']}/"
        poll(lambda: api(http, "GET", path), lambda r: r["status"] == "DONE",
             lambda r: r["status"] == "FAILED", "Student PDFs", 300)
        docs = list(WrongNotePDF.objects.filter(batch_id=batch["id"], lecture=lecture).select_related("enrollment__student"))
        require(len(docs) == 2, "Unexpected number of student PDFs")
        link = api(http, "GET", path + "download/")["url"]
        target = urlsplit(link)
        key = batch_archive_key(batch["id"], tenant.pk, docs)
        require(target.scheme == "https" and target.netloc == urlsplit(settings.R2_ENDPOINT).netloc
                and unquote(target.path) == f"/{settings.R2_STORAGE_BUCKET}/{key}", "Unexpected ZIP download destination")
        # Never send the API session's authorization header to the signed attachment.
        with requests.Session() as download:
            download.trust_env = False
            try:
                response = download.get(link, timeout=(5, 45), allow_redirects=False)
            except requests.RequestException:
                raise CommandError("Development ZIP download failed") from None
        require(response.status_code == 200 and len(response.content) < 20_000_000, "Invalid development ZIP")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = [student_filename(d) for d in docs]
            require(sorted(archive.namelist()) == sorted(names), "ZIP members differ from the two enrolled students")
            for doc in docs:
                ix = [e.pk for e in enrollments].index(doc.enrollment_id)
                verify_pdf(archive.read(student_filename(doc)), doc.enrollment.student.name, numbers[ix])
        expected_jobs = {exam_job, hw_job, *(f"wrong-note-pdf-{d.pk}" for d in docs)}
        def done_jobs():
            return set(AIJobModel.objects.filter(tenant_id=str(tenant.pk), job_id__in=expected_jobs,
                                                 status="DONE", completed_at__isnull=False).values_list("job_id", flat=True))
        poll(done_jobs, lambda ids: ids == expected_jobs, lambda _ids: False, "AI and Tools terminal receipts", 45)
        completed_jobs = list(AIJobModel.objects.filter(tenant_id=str(tenant.pk), job_id__in=expected_jobs))
        ai_ids = sorted(j.job_id for j in completed_jobs if j.job_type == "question_segmentation")
        tools_ids = sorted(j.job_id for j in completed_jobs if j.job_type == "wrong_note_pdf_generation")
        require(set(ai_ids) == {exam_job, hw_job} and set(tools_ids) == expected_jobs - set(ai_ids),
                "Completed jobs have unexpected worker types")
        seal = OpsAuditLog.objects.get(action="development.wrong-note.setup", target_tenant=tenant, result="success")
        require_acks(seal, completed_jobs)
        require(grading_state() == before, "Printing changed the approved keys or student grades")
    return {"exam_questions": 25, "homework_questions": 5, "approved_answers": 30, "students": 2, "pdf_members": 2,
            "acknowledged_segmentation_jobs": ai_ids, "acknowledged_print_jobs": tools_ids}
