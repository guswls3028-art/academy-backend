"""PostEntity/PostReply의 작성자 표시 — author_role 기반 (B-1)."""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.core.models.tenant import Tenant
from apps.domains.students.models import Student
from apps.domains.community.models import PostEntity, PostReply
from apps.domains.community.api.serializers import PostEntitySerializer, PostReplySerializer

User = get_user_model()


class TestAuthorDisplay(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="T", code="t1", is_active=True)
        self.user = User.objects.create_user(
            username="t_stu", password="pw1234",
            tenant=self.tenant, name="홍길동",
        )
        self.student = Student.objects.create(
            tenant=self.tenant, user=self.user,
            ps_number="S001", name="홍길동",
            phone="01011112222", parent_phone="01033334444", omr_code="11112222",
        )

    def _make_post(self, **kwargs):
        defaults = {
            "tenant": self.tenant,
            "post_type": "qna",
            "title": "Q",
            "content": "c",
            "created_by": self.student,
            "author_role": "student",
            "author_display_name": "홍길동",
            "status": "published",
        }
        defaults.update(kwargs)
        return PostEntity.objects.create(**defaults)

    def test_active_student_shows_name(self):
        post = self._make_post()
        data = PostEntitySerializer(post).data
        self.assertEqual(data["created_by_display"], "홍길동")
        self.assertFalse(data["created_by_deleted"])

    def test_soft_deleted_student_shows_deleted(self):
        self.student.deleted_at = timezone.now()
        self.student.save(update_fields=["deleted_at"])
        post = self._make_post()
        data = PostEntitySerializer(post).data
        self.assertEqual(data["created_by_display"], "삭제된 학생")
        self.assertTrue(data["created_by_deleted"])

    def test_hard_deleted_student_shows_deleted(self):
        """B-1: 학생 hard-delete (FK NULL) + author_role='student' → '삭제된 학생'."""
        post = self._make_post(created_by=None)  # FK NULL, author_role='student' 유지
        data = PostEntitySerializer(post).data
        self.assertEqual(data["created_by_display"], "삭제된 학생")
        self.assertTrue(data["created_by_deleted"])

    def test_admin_post_no_created_by_shows_admin(self):
        """관리자 글 (author_role='staff', FK NULL) → '관리자'."""
        post = self._make_post(
            created_by=None,
            author_role="staff",
            author_display_name=None,
            post_type="notice",
        )
        data = PostEntitySerializer(post).data
        self.assertEqual(data["created_by_display"], "관리자")
        self.assertFalse(data["created_by_deleted"])

    def test_admin_post_with_display_name(self):
        post = self._make_post(
            created_by=None,
            author_role="staff",
            author_display_name="김선생",
            post_type="notice",
        )
        data = PostEntitySerializer(post).data
        self.assertEqual(data["created_by_display"], "김선생")
        self.assertFalse(data["created_by_deleted"])

    def test_reply_hard_deleted_student(self):
        """PostReply도 동일 로직."""
        post = self._make_post()
        reply = PostReply.objects.create(
            tenant=self.tenant, post=post, content="r",
            created_by=None, author_role="student", author_display_name="홍길동",
        )
        data = PostReplySerializer(reply).data
        self.assertEqual(data["created_by_display"], "삭제된 학생")

    def test_reply_staff(self):
        post = self._make_post()
        reply = PostReply.objects.create(
            tenant=self.tenant, post=post, content="answer",
            created_by=None, author_role="staff", author_display_name="김선생",
        )
        data = PostReplySerializer(reply).data
        self.assertEqual(data["created_by_display"], "김선생")
