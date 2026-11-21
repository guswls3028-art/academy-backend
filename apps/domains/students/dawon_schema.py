"""Response shapes for Dawon projections that do not serialize a single ORM model."""


def record(**properties):
    return {"type": "object", "properties": properties, "required": list(properties)}


def array(items):
    return {"type": "array", "items": items}


def nullable(schema):
    return {**schema, "nullable": True}


def variants(*schemas):
    return {"anyOf": list(schemas)}


text = {"type": "string"}
number = {"type": "number"}
integer = {"type": "integer"}
boolean = {"type": "boolean"}
day = {"type": "string", "format": "date"}
clock = {"type": "string", "format": "time"}
fields = {"type": "object"}
rows = array(fields)
person = record(id=integer, name=text)
position = {"type": "string", "enum": ["INSTRUCTOR", "ASSISTANT"]}

group_properties = dict(id=integer, name=text, kind=text, track=text, school=text)
group_counts = dict(student_count=integer, lecture_count=integer)
group = record(**group_properties, **group_counts, instructor_name=text)
student = record(id=integer, name=text, school=text, grade=nullable(integer), phone=text, parent_phone=text)
student_page = record(count=integer, results=array(student))
directory_student = record(**student["properties"], groups=array(person))
directory_student["properties"]["courses"] = array(record(id=integer, title=text, group=integer))
directory_page = record(count=integer, results=array(directory_student))
exam_row = record(id=integer, student_id=integer, name=text, school=text, grade=nullable(integer),
                  score=nullable(number), maximum=nullable(number), status=text)
member = record(**student["properties"], eligible=boolean)
member["properties"]["course_ids"] = array(integer)  # Only shown to a director.
course = record(id=integer, title=text, period=text, school=text, start_date=nullable(day), end_date=nullable(day),
                is_active=boolean, student_count=integer, color=text, chip_label=text, lecture_time=text,
                cycle_status={"type": "string", "enum": ["upcoming", "current", "past"]})
group_detail = record(**group_properties, **group_counts, students=array(member), lectures=array(course),
                      assigned_teacher=nullable(person))
teaching = array(record(**group_properties, members=integer, courses=array(integer), recent=nullable(
    record(id=integer, lecture_id=integer, title=text, date=day, schedule_id=nullable(integer)))))
reference = record(group_id=nullable(integer), name=text, school=text, can_edit_top_reference=boolean,
                   top_reference_percent=nullable(integer))

token_pair = record(access=text, refresh=text)
learning = record(sessions=rows, notices=rows, clinics=rows, counsels=rows, school_reports=rows)
rule = {"type": "object", "properties": {"enabled": boolean, "threshold": integer}}
rules = {"type": "object", "additionalProperties": rule}
teacher_alerts = record(rules=rules, lectures=array(record(id=integer, title=text)), alerts=rows,
                        followups=rows, unavailable=array(text))
staff_directory = variants(record(staff=array(record(id=integer, name=text, position=text, classes=array(text)))),
                          record(month=text, payroll=fields, open_count=integer, records=rows))
staff_clock = record(staff=text, current=nullable(record(id=integer, date=day, start_time=clock)))
clock_request = variants(record(action={"type": "string", "enum": ["start"]}),
                         record(action={"type": "string", "enum": ["end"]}, record=integer))

attendance_group = record(id=integer, name=text, lesson_count=integer, total=integer, missing=integer, unset=integer,
                          marked=integer, absent=integer)
attendance_student = record(enrollment=integer, name=text, status=nullable(text))
attendance_day = record(date=day, groups=array(attendance_group), group_name=nullable(text),
                        lessons=array(record(id=integer, order=integer, title=text, course_id=integer, course=text,
                                             students=array(attendance_student))),
                        status_labels={"type": "object", "additionalProperties": text})
operations = variants(
    record(groups=rows, employee=nullable(fields), exams=rows), attendance_day,
    record(count=integer, rows=rows), record(count=integer, alerts=rows),
    record(student=text, course=text, exams=rows, homework=rows, counsel=rows, clinic=rows, can_follow=boolean,
           today=day, slots=rows),
    record(title=text, assigned=integer, graded=integer, average=nullable(number), highest=nullable(number),
           lowest=nullable(number), top_reference_percent=nullable(integer), distribution=array(integer), roster=array(exam_row),
           questions=array(record(number=integer, topic=text, graded_ids=array(integer), correct_ids=array(integer))), reviewed=boolean),
)
operation_patch = variants(
    record(position=position), record(exam=integer, course=integer, reviewed=boolean),
    record(followup={"type": "string", "enum": ["counsel"]}, student=integer, course=integer, day=day),
    record(followup={"type": "string", "enum": ["clinic"]}, student=integer, course=integer, session=integer),
)
operation_result = variants(record(position=position), record(reviewed=boolean), record(saved=text))
