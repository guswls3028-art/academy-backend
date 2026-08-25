from django.contrib.auth import get_user_model
from django.db.models import Max
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.program import Program
from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.enrollment.test_support import create_enrollment_fixture
from apps.domains.lectures.models import Lecture
from apps.domains.lectures.views import LectureViewSet
from apps.domains.students.test_support import create_student_fixture


User = get_user_model()


class LectureReorderApiTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Lecture order academy",
            code="lecture_order",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            name="Other academy",
            code="lecture_order_other",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="lecture_order_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
            name="Admin",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="owner",
        )
        Program.objects.update_or_create(
            tenant=self.tenant,
            defaults={
                "display_name": "Lecture order academy",
                "brand_key": "lecture_order",
                "feature_flags": {},
            },
        )

        self.first = self._lecture("First")
        self.second = self._lecture("Second")
        self.third = self._lecture("Third")
        self.past = self._lecture("Past", is_active=False)
        self.foreign = Lecture.objects.create(
            tenant=self.other_tenant,
            title="Foreign",
            name="Teacher",
            subject="science",
        )

    def _lecture(self, title, *, is_active=True):
        return Lecture.objects.create(
            tenant=self.tenant,
            title=title,
            name="Teacher",
            subject="science",
            is_active=is_active,
        )

    def _request(self, payload):
        request = self.factory.post(
            "/api/v1/lectures/lectures/reorder/",
            payload,
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return LectureViewSet.as_view({"post": "reorder"})(request)

    def test_reorder_persists_active_scope_without_moving_past_lecture(self):
        response = self._request(
            {
                "scope": "ACTIVE",
                "ordered_ids": [self.third.id, self.first.id, self.second.id],
            }
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [row["id"] for row in response.data],
            [self.third.id, self.first.id, self.second.id],
        )

        list_request = self.factory.get("/api/v1/lectures/lectures/")
        list_request.tenant = self.tenant
        force_authenticate(list_request, user=self.admin)
        list_response = LectureViewSet.as_view({"get": "list"})(list_request)
        active_ids = [
            row["id"] for row in list_response.data if row["is_active"]
        ]
        self.assertEqual(
            active_ids,
            [self.third.id, self.first.id, self.second.id],
        )
        self.past.refresh_from_db()
        self.assertGreater(self.past.display_order, 0)

    def test_list_returns_active_enrollment_count_per_tenant_lecture(self):
        for index, status in enumerate(("ACTIVE", "ACTIVE", "INACTIVE"), start=1):
            user = User.objects.create_user(
                username=f"lecture_count_student_{index}",
                password="test1234",
                tenant=self.tenant,
                name=f"Student {index}",
            )
            student = create_student_fixture(
                tenant=self.tenant,
                user=user,
                ps_number=f"LC{index:03d}",
                name=f"Student {index}",
                phone=f"0105511{index:04d}",
                parent_phone=f"0106611{index:04d}",
                omr_code=f"5511{index:04d}",
            )
            create_enrollment_fixture(
                tenant=self.tenant,
                student=student,
                lecture=self.first if index != 2 else self.second,
                status=status,
            )

        request = self.factory.get("/api/v1/lectures/lectures/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = LectureViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200, response.data)
        counts = {
            row["id"]: row["active_enrollment_count"]
            for row in response.data
        }
        self.assertEqual(counts[self.first.id], 1)
        self.assertEqual(counts[self.second.id], 1)
        self.assertEqual(counts[self.third.id], 0)
        self.assertEqual(counts[self.past.id], 0)
        self.assertNotIn(self.foreign.id, counts)

    def test_stale_or_foreign_payload_rolls_back_without_partial_updates(self):
        before = dict(
            Lecture.objects.filter(tenant=self.tenant).values_list(
                "id", "display_order"
            )
        )

        stale_response = self._request(
            {
                "scope": "ACTIVE",
                "ordered_ids": [self.second.id, self.first.id],
            }
        )
        self.assertEqual(stale_response.status_code, 409, stale_response.data)
        self.assertEqual(stale_response.data["code"], "LECTURE_ORDER_STALE")

        foreign_response = self._request(
            {
                "scope": "ACTIVE",
                "ordered_ids": [
                    self.third.id,
                    self.first.id,
                    self.foreign.id,
                ],
            }
        )
        self.assertEqual(foreign_response.status_code, 409, foreign_response.data)
        self.assertEqual(
            dict(
                Lecture.objects.filter(tenant=self.tenant).values_list(
                    "id", "display_order"
                )
            ),
            before,
        )

    def test_duplicate_ids_are_rejected_without_mutation(self):
        before = list(
            Lecture.objects.filter(tenant=self.tenant)
            .order_by("display_order", "id")
            .values_list("id", flat=True)
        )

        response = self._request(
            {
                "scope": "ACTIVE",
                "ordered_ids": [self.first.id, self.first.id, self.third.id],
            }
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            list(
                Lecture.objects.filter(tenant=self.tenant)
                .order_by("display_order", "id")
                .values_list("id", flat=True)
            ),
            before,
        )

    def test_reorder_canonicalizes_legacy_null_order_before_persisting(self):
        legacy = Lecture(
            tenant=self.tenant,
            title="Rolling deploy legacy lecture",
            name="Teacher",
            subject="science",
            display_order=None,
        )
        # bulk_create deliberately bypasses the new model save hook and models
        # an old API process that omits the expand-phase column.
        Lecture.objects.bulk_create([legacy])
        legacy.refresh_from_db()
        self.assertIsNone(legacy.display_order)

        response = self._request(
            {
                "scope": "ACTIVE",
                "ordered_ids": [
                    legacy.id,
                    self.third.id,
                    self.first.id,
                    self.second.id,
                ],
            }
        )

        self.assertEqual(response.status_code, 200, response.data)
        persisted = list(
            Lecture.objects.filter(tenant=self.tenant)
            .order_by("display_order", "id")
            .values_list("display_order", flat=True)
        )
        self.assertTrue(all(order is not None and order > 0 for order in persisted))
        self.assertEqual(len(persisted), len(set(persisted)))

    def test_new_lecture_order_is_server_owned_even_if_caller_supplies_one(self):
        supplied = Lecture.objects.create(
            tenant=self.tenant,
            title="Caller supplied order",
            name="Teacher",
            subject="science",
            display_order=self.first.display_order,
        )

        self.assertNotEqual(supplied.display_order, self.first.display_order)
        self.assertEqual(
            supplied.display_order,
            Lecture.objects.filter(tenant=self.tenant).aggregate(
                max_order=Max("display_order")
            )["max_order"],
        )
