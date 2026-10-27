"""A DRF security upgrade must not reset omitted persisted lecture flags."""

from django.test import SimpleTestCase

from apps.domains.lectures.models import Lecture
from apps.domains.lectures.serializers import LectureSerializer


class LectureDefaultCompatibilityTests(SimpleTestCase):
    def test_full_update_omission_preserves_persisted_system_flag(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                serializer = LectureSerializer(instance=Lecture(title="기존 강의", is_system=existing))
                values = serializer.to_internal_value({"title": "수정 강의", "name": "수정", "subject": "MATH"})
                self.assertNotIn("is_system", values)

    def test_create_omission_keeps_normal_lecture_default(self):
        values = LectureSerializer().to_internal_value({"title": "새 강의", "name": "신규", "subject": "MATH"})
        self.assertIs(values["is_system"], False)

    def test_explicit_flag_remains_authoritative(self):
        for provided in (False, True):
            with self.subTest(provided=provided):
                serializer = LectureSerializer(instance=Lecture(title="기존 강의", is_system=not provided))
                values = serializer.to_internal_value({
                    "title": "수정 강의", "name": "수정", "subject": "MATH", "is_system": provided,
                })
                self.assertIs(values["is_system"], provided)

    def test_partial_update_also_omits_missing_flag(self):
        serializer = LectureSerializer(instance=Lecture(title="기존 강의", is_system=True), partial=True)
        self.assertNotIn("is_system", serializer.to_internal_value({"name": "수정 표시명"}))
