from types import SimpleNamespace
from unittest.mock import patch

from django.http import QueryDict
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.domains.fees.views import FeeDashboardView
from apps.domains.homework_results.views.homework_view import HomeworkViewSet
from apps.domains.community.api.views.admin_views import AdminPostViewSet
from apps.domains.community.api.views.platform_inbox_views import PlatformInboxListView
from apps.domains.exams.views.exam_view import ExamViewSet
from apps.core.product_analytics.views import ProductUsageOverviewView
from apps.domains.landing_public.api.views.stats_views import PublicCommunityStatsView
from apps.domains.matchup.views import SimilarProblemView
from apps.domains.messaging.views.log_views import NotificationLogListView
from apps.domains.messaging.views.scheduled_views import ScheduledNotificationListView
from apps.domains.results.views.admin_result_fact_view import AdminResultFactView
from apps.domains.results.views.question_stats_views import ExamTopWrongQuestionsView
from apps.domains.results.views.wrong_note_view import WrongNoteView
from apps.domains.students.views.enrollment_matrix_view import StudentEnrollmentMatrixView
from apps.domains.video.views.event_views import VideoPlaybackEventViewSet
from apps.support.analytics.views import ExamAnalyticsTopWrongView


def _request(query: str):
    return SimpleNamespace(query_params=QueryDict(query), tenant=1)


class NumericQueryBoundaryTests(SimpleTestCase):
    def test_malformed_numbers_fail_as_validation_errors_before_domain_queries(self):
        cases = (
            (ExamAnalyticsTopWrongView().get, _request("limit=many"), (1,)),
            (FeeDashboardView().get, _request("month=13"), ()),
            (AdminResultFactView().get, _request("exam_id=nope"), ()),
            (NotificationLogListView().get, _request("page=first"), ()),
            (VideoPlaybackEventViewSet().risk, _request("video=nope"), ()),
            (AdminPostViewSet().list, _request("lecture_id=nope"), ()),
            (ProductUsageOverviewView().get, _request("days=often"), ()),
            (PublicCommunityStatsView().get, _request("days=forever"), ()),
            (PlatformInboxListView().get, _request("page=first"), ()),
            (WrongNoteView().get, _request("enrollment_id=nope"), ()),
            (
                StudentEnrollmentMatrixView().get,
                _request("lecture_id=nope"),
                (1,),
            ),
        )

        for handler, request, args in cases:
            with self.subTest(handler=handler.__qualname__), self.assertRaises(ValidationError):
                handler(request, *args)

    @patch(
        "apps.domains.messaging.views.scheduled_views.can_send_messages",
        return_value=True,
    )
    def test_scheduled_notification_pagination_rejects_malformed_numbers(
        self,
        _can_send_messages,
    ):
        with self.assertRaises(ValidationError):
            ScheduledNotificationListView().get(_request("page_size=many"))

    def test_filter_viewsets_do_not_turn_malformed_filters_into_empty_lists(self):
        cases = (
            (HomeworkViewSet, "session_id=oops"),
            (ExamViewSet, "lecture_id=oops"),
        )

        for viewset_type, query in cases:
            with self.subTest(viewset=viewset_type.__name__):
                viewset = viewset_type()
                viewset.request = _request(query)
                with self.assertRaises(ValidationError):
                    viewset.get_queryset()

    def test_filter_viewsets_reject_malformed_boolean_flags(self):
        cases = (
            (HomeworkViewSet, "include_removed=yes"),
            (ExamViewSet, "include_inactive=maybe"),
        )

        for viewset_type, query in cases:
            with self.subTest(viewset=viewset_type.__name__):
                viewset = viewset_type()
                viewset.request = _request(query)
                with self.assertRaises(ValidationError):
                    viewset.get_queryset()

    @patch(
        "apps.domains.results.views.question_stats_views._verify_exam_tenant",
        return_value=None,
    )
    def test_top_wrong_count_rejects_negative_values(self, _verify_exam_tenant):
        with self.assertRaises(ValidationError):
            ExamTopWrongQuestionsView().get(_request("n=-1"), exam_id=1)

    @patch("apps.domains.matchup.views._is_tenant_staff", return_value=True)
    def test_matchup_similarity_rejects_malformed_top_k(self, _is_tenant_staff):
        request = SimpleNamespace(
            body=b'{"top_k":"many"}',
            GET=QueryDict(""),
            tenant=SimpleNamespace(id=1),
            user=SimpleNamespace(id=1),
        )

        response = SimilarProblemView().post(request, problem_id=1)

        self.assertEqual(response.status_code, 400)
