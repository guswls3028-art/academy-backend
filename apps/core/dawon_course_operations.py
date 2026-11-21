"""The public director demo uses the tenant-scoped teaching views for course operations."""
import re

_ID = r"[1-9]\d{0,17}"
# Keep unrelated staff records, account management, external delivery and paid jobs outside this surface.
_READ = re.compile(
    rf"(?:lectures/(?:lectures|sessions|attendance|sections|section-assignments)(?:/[\w/-]+)?"
    rf"|enrollments(?:/[\w/-]+)?|exams(?:/[\w/-]+)?|homeworks?(?:/[\w/-]+)?"
    rf"|results/admin/(?:sessions|exams|attempt-history|student-grades|clinic-targets)(?:/[\w/-]+)?"
    rf"|submissions(?:/{_ID})?|media/(?:videos|video-permissions|video-progress)(?:/[\w/-]+)?"
    rf"|students/(?:teacher-alerts|class-groups/for-lecture/{_ID})"
    rf"|community/(?:posts|categories|scope-nodes)(?:/[\w/-]+)?|community/admin/posts)"
)
_OPERATIONS = {
    "POST": re.compile(
        rf"(?:lectures/sessions(?:/with-schedules|/{_ID}/schedules)?"
        rf"|lectures/attendance/(?:bulk_create|bulk_set_present|bulk_undo_present)"
        rf"|enrollments/session-enrollments(?:/bulk_create)?|exams(?:/{_ID}/(?:spawn-regular|questions/init|structure/ensure|save-as-template|enrollments)|/answer-keys)?"
        rf"|homeworks(?:/{_ID}/(?:save-as-template|source-exam))?|homework/(?:assignments|enrollments|scores)"
        rf"|results/admin/sessions/{_ID}/(?:score-draft/commit|reorder|exams)|media/videos/(?:youtube|reorder|folders|bulk-policy))"
    ),
    "PATCH": re.compile(
        rf"(?:lectures/(?:attendance|sessions)/{_ID}|exams/{_ID}(?:/omr/essay-score-marks)?"
        rf"|homeworks/{_ID}(?:/question-grading)?|homework/scores/(?:{_ID}|quick)"
        rf"|results/admin/(?:sessions/{_ID}/score-correction|exams/{_ID}/enrollments/{_ID}(?:/(?:score|subjective|objective|manual-answers|items/{_ID}))?)"
        rf"|media/videos/{_ID}(?:/placements)?|students/(?:teacher-alerts|class-groups/for-lecture/{_ID}))"
    ),
    "PUT": re.compile(
        rf"(?:lectures/sessions/{_ID}/schedules|exams/answer-keys/{_ID}|homework/(?:enrollments|assignments)"
        rf"|results/admin/sessions/{_ID}/(?:score-draft|total-config))"
    ),
}


def allows_course_operation(request):
    path = request.path.removeprefix("/api/v1/").removesuffix("/")
    if request.method in {"GET", "HEAD"}:
        return bool(_READ.fullmatch(path))
    if request.method == "POST" and path == "community/posts":
        return isinstance(request.data, dict) and request.data.get("post_type") == "notice"
    if request.method == "PATCH" and (post := re.fullmatch(rf"community/posts/({_ID})", path)):
        from apps.domains.community.models import PostEntity
        return (isinstance(request.data, dict)
                and not set(request.data) - {"title", "content", "node_ids", "is_urgent", "is_pinned"}
                and PostEntity.objects.filter(pk=post[1], tenant=request.tenant, post_type="notice").exists())
    pattern = _OPERATIONS.get(request.method)
    return bool(pattern and pattern.fullmatch(path))
