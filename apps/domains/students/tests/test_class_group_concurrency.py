"""Row-lock regression for simultaneous creation from the permanent roster."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.students.models import ClassGroup


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class ClassGroupConcurrentCreationTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_same_period_creates_one_course(self):
        tenant = Tenant.objects.create(id=719, code="dawon", name="다원")
        actor = get_user_model().objects.create_user(username="cycle-owner", tenant=tenant)
        TenantMembership.ensure_active(tenant=tenant, user=actor, role="owner")
        group = ClassGroup.objects.create(tenant=tenant, name="연합반", kind="union", track="SKY")
        url = f"/api/v1/students/class-groups/{group.pk}/lectures/"
        barrier = Barrier(2, timeout=10)

        def create(index):
            close_old_connections()
            try:
                req = APIRequestFactory().post(url, {
                    "title": f"동시 생성 {index}", "period": "vacation",
                    "start_date": "2027-01-01", "end_date": "2027-03-01",
                }, format="json")
                req.tenant = tenant
                force_authenticate(req, user=actor)
                view = resolve(url)
                barrier.wait()
                return view.func(req, **view.kwargs).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(create, (0, 1))), [201, 400])
        self.assertEqual(group.course_links.count(), 1)
