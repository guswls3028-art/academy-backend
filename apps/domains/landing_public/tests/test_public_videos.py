import io
from importlib import import_module
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, close_old_connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.landing_public.api.views import video_views as views
from apps.domains.landing_public.models import PublicResourceBoardAccess, PublicVideo, PublicVideoEvent, PublicVideoShowcase
from apps.domains.landing_public.services.youtube import YouTubeUnavailable, normalize_youtube_url


CONTENT = {"title": "과학을 이해하는 방법", "summary": "신민 선생님의 이야기", "category": "interview", "format": "landscape"}
URL = "https://youtu.be/xgjL2HyrdFM?si=ignored"


def metadata(url):
    return {**normalize_youtube_url(url), "source_title": "원본 제목", "channel_title": "두각",
            "thumbnail_url": "https://i.ytimg.com/vi/xgjL2HyrdFM/hqdefault.jpg"}


class PublicVideoTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="qa-video", code="qa-video", is_active=True)
        self.other = Tenant.objects.create(name="qa-other", code="qa-video-other", is_active=True)
        self.one = self.user("qa-one", "owner")
        self.two = self.user("qa-two", "admin")
        self.staff = self.user("qa-staff", "teacher", is_superuser=True)
        self.access = PublicResourceBoardAccess.objects.create(tenant=self.tenant, publisher_one=self.one, publisher_two=self.two)
        patcher = patch.object(views, "resolve_youtube", side_effect=metadata)
        self.lookup = patcher.start()
        self.addCleanup(patcher.stop)

    def user(self, name, role, **kwargs):
        user = get_user_model().objects.create_user(username=name, password="qa-only", name=name, tenant=self.tenant, **kwargs)
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
        return user

    def call(self, view, method="get", data=None, user=None, tenant=None, pk=None):
        request = getattr(self.factory, method)("/qa/", data=data or {}, format="json")
        request.tenant = tenant or self.tenant
        if user:
            force_authenticate(request, user=user)
        return view.as_view()(request, **({"pk": pk} if pk else {}))

    def create(self, url=URL, **extra):
        body = {"url": url, "request_id": str(uuid.uuid4()), "draft_content": CONTENT, **extra}
        response = self.call(views.ManageVideoListView, "post", body, user=self.one)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def publish(self, row, hero=False, showcase_revision=0):
        return self.call(views.ManageVideoPublishView, "post", {
            "expected_revision": row["revision"], "expected_showcase_revision": showcase_revision, "set_as_hero": hero,
        }, user=self.two, pk=row["id"])

    def test_complete_two_publisher_journey_and_public_snapshot(self):
        row = self.create()
        self.assertEqual(self.call(views.PublicVideoListView).data["count"], 0)
        self.assertEqual(self.call(views.PublicVideoDetailView, pk=row["id"]).status_code, 404)
        reloaded = self.call(views.ManageVideoDetailView, user=self.two, pk=row["id"])
        self.assertEqual(reloaded.data["draft_content"], CONTENT)
        published = self.publish(row, hero=True)
        self.assertEqual(published.status_code, 200, published.data)
        public = self.call(views.PublicVideoDetailView, pk=row["id"])
        self.assertEqual(public.data["title"], CONTENT["title"])
        self.assertEqual(public["Cache-Control"], "no-store")
        self.assertNotIn("draft_content", public.data)
        edited = self.call(views.ManageVideoDetailView, "patch", {
            "expected_revision": published.data["video"]["revision"],
            "draft_content": {**CONTENT, "title": "새 제목"},
        }, user=self.one, pk=row["id"])
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(self.call(views.PublicVideoDetailView, pk=row["id"]).data["title"], CONTENT["title"])
        republished = self.publish(edited.data, hero=True, showcase_revision=1)
        self.assertEqual(republished.status_code, 200)
        self.assertEqual(self.call(views.PublicVideoShowcaseView).data["hero"]["title"], "새 제목")
        second = self.create("https://www.youtube.com/shorts/Q70aZV1TH1Y")
        swapped = self.publish(second, hero=True, showcase_revision=1)
        self.assertEqual(swapped.data["showcase_revision"], 2)
        self.assertEqual(self.call(views.PublicVideoShowcaseView).data["hero"]["id"], second["id"])
        hidden = self.call(views.ManageVideoUnpublishView, "post", {
            "expected_revision": swapped.data["video"]["revision"], "expected_showcase_revision": 2,
        }, user=self.one, pk=second["id"])
        self.assertEqual(hidden.status_code, 200)
        self.assertIsNone(self.call(views.PublicVideoShowcaseView).data["hero"])
        self.assertEqual(self.call(views.PublicVideoDetailView, pk=second["id"]).status_code, 404)
        self.assertEqual(self.call(views.PublicVideoListView).data["count"], 1)
        self.assertEqual(self.call(views.ManageVideoDetailView, user=self.two, pk=second["id"]).data["draft_content"], CONTENT)
        self.assertTrue(PublicVideoEvent.objects.filter(action="hero", actor=self.two).exists())

    def test_duplicate_urls_and_lost_create_response_are_recoverable(self):
        request_id = str(uuid.uuid4())
        row = self.create(request_id=request_id)
        self.lookup.side_effect = YouTubeUnavailable()
        repeated = self.call(views.ManageVideoListView, "post", {
            "url": URL, "request_id": request_id, "draft_content": CONTENT,
        }, user=self.one)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.data["id"], row["id"])
        duplicate = self.call(views.ManageVideoListView, "post", {
            "url": "https://www.youtube.com/watch?v=xgjL2HyrdFM", "request_id": str(uuid.uuid4()), "draft_content": CONTENT,
        }, user=self.two)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.data["existing_id"], row["id"])
        changed = self.call(views.ManageVideoListView, "post", {
            "url": URL, "request_id": request_id, "draft_content": {**CONTENT, "title": "다른 요청"},
        }, user=self.one)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(PublicVideo.objects.count(), 1)

    def test_metadata_outage_saves_draft_but_preserves_public_on_publish_failure(self):
        self.lookup.side_effect = YouTubeUnavailable()
        draft = self.create()
        self.assertFalse(draft["metadata_ready"])
        self.assertEqual(self.publish(draft).status_code, 503)
        self.lookup.side_effect = metadata
        published = self.publish(draft, hero=True)
        self.assertEqual(published.status_code, 200)
        self.lookup.side_effect = YouTubeUnavailable()
        self.assertEqual(self.publish(published.data["video"], hero=True, showcase_revision=1).status_code, 503)
        self.assertEqual(self.call(views.PublicVideoShowcaseView).data["hero"]["id"], draft["id"])

    def test_conflicts_do_not_overwrite_and_hero_requires_published_same_tenant(self):
        row = self.create()
        published = self.publish(row, hero=True)
        stale = self.call(views.ManageVideoDetailView, "patch", {"expected_revision": 1, "draft_content": {**CONTENT, "title": "stale"}}, user=self.two, pk=row["id"])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.publish(published.data["video"], hero=False, showcase_revision=0).status_code, 409)
        draft = self.create("https://youtu.be/YYTULV4h4Io")
        result = self.call(views.ManageVideoShowcaseView, "patch", {"expected_revision": 1, "hero_video_id": draft["id"]}, user=self.one)
        self.assertEqual(result.status_code, 404)
        self.assertEqual(self.call(views.PublicVideoShowcaseView).data["hero"]["id"], row["id"])

    def test_custom_presentation_and_category_persist_without_publishing_drafts(self):
        self.assertEqual(self.call(views.PublicVideoShowcaseView).data, {"hero": None, "title": "영상", "description": ""})
        custom = {**CONTENT, "category_label": "실험 기록"}
        row = self.create(draft_content=custom)
        published = self.publish(row, hero=True)
        updated = self.call(views.ManageVideoShowcaseView, "patch", {
            "expected_revision": 1, "title": "영상 자료", "description": "직접 작성한 소개",
        }, user=self.one)
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["hero_video_id"], row["id"])
        self.assertEqual(updated.data["revision"], 2)
        self.assertEqual(self.call(views.ManageVideoShowcaseView, user=self.two).data, updated.data)
        public = self.call(views.PublicVideoShowcaseView).data
        self.assertEqual((public["title"], public["description"], public["hero"]["category_label"]), ("영상 자료", "직접 작성한 소개", "실험 기록"))
        stale = self.call(views.ManageVideoShowcaseView, "patch", {"expected_revision": 1, "title": "stale"}, user=self.two)
        self.assertEqual(stale.status_code, 409)
        clear = self.call(views.ManageVideoShowcaseView, "patch", {"expected_revision": 2, "description": ""}, user=self.two)
        self.assertEqual(clear.data["title"], "영상 자료")
        self.assertEqual(clear.data["description"], "")
        saved = self.call(views.ManageVideoDetailView, "patch", {
            "expected_revision": published.data["video"]["revision"], "draft_content": {**custom, "category_label": ""},
        }, user=self.two, pk=row["id"])
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.call(views.PublicVideoDetailView, pk=row["id"]).data["category_label"], "실험 기록")
        self.assertEqual(self.publish(saved.data, hero=True, showcase_revision=3).status_code, 200)
        self.assertEqual(self.call(views.PublicVideoDetailView, pk=row["id"]).data["category_label"], "")
        for values in ({"title": ""}, {"title": "a" * 61}, {"description": "a" * 221}):
            self.assertEqual(self.call(views.ManageVideoShowcaseView, "patch", {"expected_revision": 3, **values}, user=self.one).status_code, 400)
        invalid = self.call(views.ManageVideoListView, "post", {"url": URL, "request_id": str(uuid.uuid4()), "draft_content": {**CONTENT, "category_label": "a" * 41}}, user=self.one)
        self.assertEqual(invalid.status_code, 400)

    def test_delete_clears_hero_and_all_read_surfaces_retry_and_reregister_are_safe(self):
        request_id = str(uuid.uuid4())
        row = self.create(request_id=request_id)
        published = self.publish(row, hero=True)
        payload = {"expected_revision": published.data["video"]["revision"], "expected_showcase_revision": 1}
        self.assertEqual(self.call(views.ManageVideoDetailView, "delete", payload, user=self.one, pk=row["id"]).status_code, 204)
        self.assertEqual(self.call(views.PublicVideoListView).data["count"], 0)
        self.assertEqual(self.call(views.ManageVideoListView, user=self.two).data["count"], 0)
        self.assertEqual(self.call(views.PublicVideoDetailView, pk=row["id"]).status_code, 404)
        self.assertEqual(self.call(views.ManageVideoDetailView, user=self.two, pk=row["id"]).status_code, 404)
        self.assertIsNone(self.call(views.PublicVideoShowcaseView).data["hero"])
        self.assertEqual(self.call(views.ManageVideoShowcaseView, user=self.two).data["revision"], 2)
        self.assertEqual(self.call(views.ManageVideoDetailView, "delete", payload, user=self.two, pk=row["id"]).status_code, 204)
        self.assertEqual(PublicVideoEvent.objects.filter(video_id=row["id"], action="delete").count(), 1)
        self.assertEqual(PublicVideo.objects.get(pk=row["id"]).draft_content, CONTENT)
        replay = self.call(views.ManageVideoListView, "post", {"url": URL, "request_id": request_id, "draft_content": CONTENT}, user=self.one)
        self.assertEqual(replay.status_code, 409)
        replacement = self.create()
        self.assertNotEqual(replacement["id"], row["id"])
        self.assertEqual(self.publish(replacement, hero=True, showcase_revision=2).status_code, 200)
        self.assertEqual(self.call(views.PublicVideoShowcaseView).data["hero"]["id"], replacement["id"])
        # A delayed retry must never clear the newly registered hero.
        self.assertEqual(self.call(views.ManageVideoDetailView, "delete", payload, user=self.one, pk=row["id"]).status_code, 204)
        self.assertEqual(self.call(views.PublicVideoShowcaseView).data["hero"]["id"], replacement["id"])

    def test_old_editor_preserves_new_custom_category(self):
        row = self.create(draft_content={**CONTENT, "category_label": "news"})
        old_client = self.call(views.ManageVideoDetailView, "patch", {
            "expected_revision": row["revision"], "draft_content": {**CONTENT, "title": "구버전에서 수정"},
        }, user=self.two, pk=row["id"])
        self.assertEqual(old_client.status_code, 200)
        self.assertEqual(old_client.data["draft_content"]["category_label"], "news")
        self.assertEqual(self.publish(old_client.data).status_code, 200)
        self.assertEqual(self.call(views.PublicVideoDetailView, pk=row["id"]).data["category_label"], "news")

    def test_delete_stale_revision_permission_and_tenant_boundaries(self):
        row = self.create()
        published = self.publish(row, hero=True)
        fresh = {"expected_revision": published.data["video"]["revision"], "expected_showcase_revision": 1}
        for stale in ({**fresh, "expected_revision": 1}, {**fresh, "expected_showcase_revision": 0}):
            self.assertEqual(self.call(views.ManageVideoDetailView, "delete", stale, user=self.one, pk=row["id"]).status_code, 409)
        for user in (None, self.staff):
            self.assertIn(self.call(views.ManageVideoDetailView, "delete", fresh, user=user, pk=row["id"]).status_code, (401, 403))
            self.assertIn(self.call(views.ManageVideoShowcaseView, "patch", {"expected_revision": 1, "title": "denied"}, user=user).status_code, (401, 403))
        for user, role in ((self.one, "owner"), (self.two, "admin")):
            TenantMembership.ensure_active(tenant=self.other, user=user, role=role)
        PublicResourceBoardAccess.objects.create(tenant=self.other, publisher_one=self.one, publisher_two=self.two)
        self.assertEqual(self.call(views.ManageVideoDetailView, "delete", fresh, tenant=self.other, user=self.one, pk=row["id"]).status_code, 404)
        self.assertEqual(self.call(views.PublicVideoShowcaseView).data["hero"]["id"], row["id"])
        self.assertEqual(PublicVideoEvent.objects.filter(action="delete").count(), 0)

    def test_permission_denial_and_tenant_isolation(self):
        row = self.create()
        self.publish(row)
        for user in (None, self.staff):
            self.assertFalse(self.call(views.PublicVideoCapabilityView, user=user).data["can_publish"])
            self.assertIn(self.call(views.ManageVideoListView, user=user).status_code, (401, 403))
        self.assertEqual(self.call(views.PublicVideoListView, tenant=self.other).data["count"], 0)
        self.assertEqual(self.call(views.PublicVideoDetailView, tenant=self.other, pk=row["id"]).status_code, 404)
        self.assertIn(self.call(views.ManageVideoDetailView, tenant=self.other, user=self.one, pk=row["id"]).status_code, (401, 403))
        TenantMembership.objects.filter(tenant=self.tenant, user=self.one).update(is_active=False)
        self.assertFalse(self.call(views.PublicVideoCapabilityView, user=self.one).data["can_publish"])
        self.assertEqual(self.call(views.ManageVideoDetailView, "patch", {"expected_revision": 2, "draft_content": CONTENT}, user=self.one, pk=row["id"]).status_code, 403)
        self.tenant.is_active = False
        self.assertEqual(self.call(views.PublicVideoListView).status_code, 404)

    def test_neutralization_changes_only_matching_bootstrap_fields_and_is_repeatable(self):
        migration = import_module("apps.domains.landing_public.migrations.0013_neutral_launch_video_copy")
        self.tenant.code = "godmin"
        self.tenant.save(update_fields=["code"])
        title, summary, category, video_format = migration.LAUNCH_COPY["xgjL2HyrdFM"]
        original = {"title": title, "summary": summary, "category": category, "format": video_format}
        row = self.create(draft_content=original)
        self.publish(row)
        video = PublicVideo.objects.get(pk=row["id"])
        video.draft_content = {**original, "title": "내 제목", "summary": "사용자가 수정한 소개"}
        video.save(update_fields=["draft_content"])
        # Same bootstrap-shaped content outside godmin is out of scope.
        foreign = PublicVideo.objects.create(tenant=self.other, youtube_video_id="xgjL2HyrdFM", canonical_url=URL,
                                            request_id=uuid.uuid4(), draft_content=original, creation_content=original)
        migration.neutralize_launch_copy(apps, SimpleNamespace(connection=connection))
        video.refresh_from_db(); foreign.refresh_from_db()
        self.assertEqual(video.draft_content, {**original, "title": "내 제목", "summary": "사용자가 수정한 소개"})
        self.assertEqual(video.published_content, {**original, "summary": ""})
        self.assertEqual(video.creation_content, original)
        self.assertEqual(foreign.draft_content, original)
        revision = video.revision
        migration.neutralize_launch_copy(apps, SimpleNamespace(connection=connection))
        video.refresh_from_db()
        self.assertEqual(video.revision, revision)
        self.assertEqual(PublicVideoEvent.objects.filter(video=video, action="neutralize").count(), 1)

    @patch("apps.domains.landing_public.management.commands.seed_godmin_public_videos.resolve_youtube", side_effect=metadata)
    def test_launch_plan_and_apply_preserve_existing_editorial_choices(self, lookup):
        self.tenant.code = "godmin"
        self.tenant.save(update_fields=["code"])
        output = io.StringIO()
        call_command("seed_godmin_public_videos", actor_id=self.one.pk, stdout=output)
        self.assertEqual(PublicVideo.objects.count(), 0)
        self.assertEqual(lookup.call_count, 0)
        call_command("seed_godmin_public_videos", actor_id=self.one.pk, apply=True, stdout=output)
        self.assertEqual(PublicVideo.objects.filter(status="published").count(), 4)
        hero = PublicVideoShowcase.objects.get(tenant=self.tenant)
        self.assertEqual(hero.hero_video.youtube_video_id, "xgjL2HyrdFM")
        row = hero.hero_video
        row.draft_content = {**CONTENT, "title": "사용자가 작성한 제목"}
        row.status = "unpublished"
        row.save()
        hero.hero_video = None
        hero.revision += 1
        hero.save()
        events = PublicVideoEvent.objects.count()
        call_command("seed_godmin_public_videos", actor_id=self.one.pk, apply=True, stdout=output)
        row.refresh_from_db(); hero.refresh_from_db()
        self.assertEqual(PublicVideo.objects.count(), 4)
        self.assertEqual(row.draft_content["title"], "사용자가 작성한 제목")
        self.assertEqual(row.status, "unpublished")
        self.assertIsNone(hero.hero_video_id)
        self.assertEqual(PublicVideoEvent.objects.count(), events)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock contract")
class PublicVideoRaceTests(TransactionTestCase):
    setUp = PublicVideoTests.setUp
    user = PublicVideoTests.user
    call = PublicVideoTests.call
    create = PublicVideoTests.create
    publish = PublicVideoTests.publish

    def race(self, work):
        barrier = Barrier(2)

        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return work(index)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, index) for index in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_same_youtube_created_once_across_two_publishers(self):
        users = (self.one, self.two)
        statuses = self.race(lambda index: self.call(views.ManageVideoListView, "post", {
            "url": URL, "request_id": str(uuid.uuid4()), "draft_content": CONTENT,
        }, user=users[index]).status_code)
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(PublicVideo.objects.count(), 1)

    def test_competing_heroes_reject_stale_showcase_without_partial_publish(self):
        rows = (self.create(), self.create("https://youtu.be/YYTULV4h4Io"))
        statuses = self.race(lambda index: self.call(views.ManageVideoPublishView, "post", {
            "expected_revision": rows[index]["revision"], "expected_showcase_revision": 0, "set_as_hero": True,
        }, user=(self.one, self.two)[index], pk=rows[index]["id"]).status_code)
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(PublicVideo.objects.filter(status="published").count(), 1)
        self.assertEqual(PublicVideoShowcase.objects.get(tenant=self.tenant).hero_video.status, "published")

    def test_delete_and_edit_cannot_overwrite_each_other(self):
        row = self.create()
        def work(index):
            if index:
                return self.call(views.ManageVideoDetailView, "delete", {"expected_revision": 1, "expected_showcase_revision": 0}, user=self.two, pk=row["id"]).status_code
            return self.call(views.ManageVideoDetailView, "patch", {"expected_revision": 1, "draft_content": {**CONTENT, "title": "최신 내용"}}, user=self.one, pk=row["id"]).status_code
        statuses = sorted(self.race(work))
        self.assertIn(statuses, ([200, 409], [204, 404]))
        saved = PublicVideo.objects.get(pk=row["id"])
        self.assertEqual(saved.revision, 2)
        self.assertEqual(saved.draft_content["title"], CONTENT["title"] if saved.status == "deleted" else "최신 내용")


class YouTubeLookupTests(SimpleTestCase):
    def test_normalization_and_untrusted_destinations(self):
        self.assertEqual(normalize_youtube_url(URL)["canonical_url"], "https://www.youtube.com/watch?v=xgjL2HyrdFM")
        self.assertEqual(normalize_youtube_url("https://youtube.com/shorts/Q70aZV1TH1Y?si=x")["format"], "vertical")
        for url in ("file:///etc/passwd", "https://youtube.com.evil.test/watch?v=xgjL2HyrdFM", "https://youtube.com@127.0.0.1/", "https://youtube.com:443/watch?v=xgjL2HyrdFM", "https://youtu.be/a", "https://youtube.com/watch?v=xgjL2HyrdFM&v=YYTULV4h4Io", "javascript:alert(1)", "https://youtube.com/", {}, None):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                normalize_youtube_url(url)
