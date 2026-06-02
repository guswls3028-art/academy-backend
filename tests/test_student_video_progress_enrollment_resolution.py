from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, User
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.student_app.media.views import StudentVideoProgressView
from apps.domains.students.models import Student
from apps.domains.video.models import Video, VideoProgress


class StudentVideoProgressEnrollmentResolutionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="student-video-progress",
            name="Student Video Progress",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="student-video-progress-user",
            password="testpass123",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.user,
            name="Video Student",
            ps_number="SVP-001",
            omr_code="12345678",
            parent_phone="01012345678",
            school_type="HIGH",
        )
        self.old_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Old Lecture",
            name="Old Lecture",
            subject="MATH",
        )
        self.target_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Target Lecture",
            name="Target Lecture",
            subject="MATH",
        )
        self.old_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.old_lecture,
            status="ACTIVE",
        )
        self.target_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.target_lecture,
            status="ACTIVE",
        )
        self.target_session = Session.objects.create(
            lecture=self.target_lecture,
            title="Target Session",
            order=1,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            session=self.target_session,
            title="Target Video",
            status=Video.Status.READY,
            duration=100,
        )

    def _post_progress(self, payload):
        request = self.factory.post(
            f"/api/v1/student/video/videos/{self.video.id}/progress/",
            payload,
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return StudentVideoProgressView.as_view()(request, video_id=self.video.id)

    def test_progress_without_explicit_enrollment_uses_video_lecture_enrollment(self):
        response = self._post_progress({
            "progress": 50,
            "last_position": 37,
            "completed": False,
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["enrollment_id"], self.target_enrollment.id)

        progress = VideoProgress.objects.get(video=self.video)
        self.assertEqual(progress.enrollment_id, self.target_enrollment.id)
        self.assertEqual(progress.last_position, 37)
        self.assertAlmostEqual(progress.progress, 0.5)
        self.assertFalse(
            VideoProgress.objects.filter(
                video=self.video,
                enrollment=self.old_enrollment,
            ).exists()
        )

    def test_progress_body_enrollment_id_is_validated_against_video_lecture(self):
        response = self._post_progress({
            "enrollment_id": self.old_enrollment.id,
            "progress": 50,
        })

        self.assertEqual(response.status_code, 400)
        self.assertFalse(VideoProgress.objects.filter(video=self.video).exists())
