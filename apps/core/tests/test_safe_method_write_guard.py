from __future__ import annotations

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session as DatabaseSession
from django.db import transaction
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase

from apps.core.middleware.safe_method_write import (
    SafeMethodDatabaseWriteMiddleware,
    SafeMethodWriteError,
    sql_write_operation,
)
from apps.core.models import Tenant


class SafeMethodDatabaseWriteMiddlewareTests(TransactionTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_select_is_allowed(self):
        Tenant.objects.create(code="safe-read-existing", name="Safe read")
        middleware = SafeMethodDatabaseWriteMiddleware(
            lambda _request: Tenant.objects.filter(code="safe-read-existing").count()
        )

        result = middleware(self.factory.get("/safe-read/"))

        self.assertEqual(result, 1)

    def test_get_insert_is_blocked_before_database_mutation(self):
        middleware = SafeMethodDatabaseWriteMiddleware(
            lambda _request: Tenant.objects.create(
                code="unsafe-get-create",
                name="Unsafe GET create",
            )
        )

        with self.assertRaisesMessage(
            SafeMethodWriteError,
            "safe HTTP method GET attempted database INSERT",
        ):
            middleware(self.factory.get("/unsafe-get/"))

        self.assertFalse(Tenant.objects.filter(code="unsafe-get-create").exists())

    def test_head_and_options_inserts_are_blocked_before_database_mutation(self):
        for method in ("head", "options"):
            code = f"unsafe-{method}-create"
            middleware = SafeMethodDatabaseWriteMiddleware(
                lambda _request, code=code: Tenant.objects.create(
                    code=code,
                    name=f"Unsafe {method.upper()} create",
                )
            )

            with self.subTest(method=method):
                with self.assertRaisesMessage(
                    SafeMethodWriteError,
                    f"safe HTTP method {method.upper()} attempted database INSERT",
                ):
                    middleware(getattr(self.factory, method)(f"/unsafe-{method}/"))
                self.assertFalse(Tenant.objects.filter(code=code).exists())

    def test_post_insert_remains_allowed(self):
        middleware = SafeMethodDatabaseWriteMiddleware(
            lambda _request: Tenant.objects.create(
                code="allowed-post-create",
                name="Allowed POST create",
            )
        )

        tenant = middleware(self.factory.post("/allowed-post/"))

        self.assertEqual(tenant.code, "allowed-post-create")

    def test_lazy_get_stream_cannot_write_after_response_returns(self):
        def stream():
            Tenant.objects.create(
                code="unsafe-stream-create",
                name="Unsafe stream create",
            )
            yield b"unsafe"

        middleware = SafeMethodDatabaseWriteMiddleware(
            lambda _request: StreamingHttpResponse(stream())
        )
        response = middleware(self.factory.get("/unsafe-stream/"))

        with self.assertRaisesMessage(
            SafeMethodWriteError,
            "safe HTTP method GET attempted database INSERT",
        ):
            b"".join(response.streaming_content)

        self.assertFalse(Tenant.objects.filter(code="unsafe-stream-create").exists())

    def test_lazy_async_get_stream_cannot_write_after_response_returns(self):
        async def stream():
            await sync_to_async(Tenant.objects.create)(
                code="unsafe-async-stream-create",
                name="Unsafe async stream create",
            )
            yield b"unsafe"

        async def consume(response):
            return [chunk async for chunk in response.streaming_content]

        middleware = SafeMethodDatabaseWriteMiddleware(
            lambda _request: StreamingHttpResponse(stream())
        )
        response = middleware(self.factory.get("/unsafe-async-stream/"))

        with self.assertRaisesMessage(
            SafeMethodWriteError,
            "safe HTTP method GET attempted database INSERT",
        ):
            async_to_sync(consume)(response)

        self.assertFalse(
            Tenant.objects.filter(code="unsafe-async-stream-create").exists()
        )

    def test_blocked_write_does_not_poison_the_surrounding_transaction(self):
        middleware = SafeMethodDatabaseWriteMiddleware(
            lambda _request: Tenant.objects.create(
                code="unsafe-atomic-create",
                name="Unsafe atomic create",
            )
        )

        with transaction.atomic():
            with self.assertRaises(SafeMethodWriteError):
                middleware(self.factory.get("/unsafe-atomic/"))
            self.assertFalse(
                Tenant.objects.filter(code="unsafe-atomic-create").exists()
            )

    def test_get_session_save_is_blocked_during_response_unwind(self):
        def response_with_session_write(request):
            request.session["unsafe-safe-method-write"] = True
            return HttpResponse("unsafe")

        middleware = SafeMethodDatabaseWriteMiddleware(
            SessionMiddleware(response_with_session_write)
        )

        with self.assertRaisesMessage(
            SafeMethodWriteError,
            "safe HTTP method GET attempted database INSERT",
        ):
            middleware(self.factory.get("/unsafe-session-write/"))

        self.assertFalse(DatabaseSession.objects.exists())


class SqlWriteOperationTests(SimpleTestCase):
    def test_leading_comments_do_not_hide_write(self):
        self.assertEqual(sql_write_operation("/* trace */ -- note\n UPDATE app SET value = 1"), "UPDATE")

    def test_data_changing_cte_is_blocked(self):
        self.assertEqual(
            sql_write_operation("WITH changed AS (DELETE FROM app RETURNING id) SELECT id FROM changed"),
            "DELETE",
        )

    def test_select_is_not_classified_as_write(self):
        self.assertIsNone(sql_write_operation("SELECT id FROM app"))


class SafeMethodMiddlewareOrderingTests(SimpleTestCase):
    def test_guard_wraps_database_backed_session_response_writes(self):
        guard = "apps.core.middleware.safe_method_write.SafeMethodDatabaseWriteMiddleware"
        session = "django.contrib.sessions.middleware.SessionMiddleware"

        self.assertLess(settings.MIDDLEWARE.index(guard), settings.MIDDLEWARE.index(session))
