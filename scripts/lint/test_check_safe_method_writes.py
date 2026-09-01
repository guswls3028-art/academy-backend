from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.lint.check_safe_method_writes import find_violations


class SafeMethodWriteBoundaryTests(unittest.TestCase):
    def _violations(self, source: str):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "views.py").write_text(source, encoding="utf-8")
            return find_violations(root)

    def test_safe_handler_manager_create_is_rejected(self):
        violations = self._violations(
            "class View:\n"
            "    def get(self, request):\n"
            "        return Config.objects.get_or_create(tenant=request.tenant)\n"
        )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].handler, "get")
        self.assertEqual(violations[0].call, "Config.objects.get_or_create")

    def test_safe_handler_queryset_update_is_rejected(self):
        violations = self._violations(
            "class View:\n"
            "    def list(self, request):\n"
            "        return Config.objects.filter(active=True).update(seen=True)\n"
        )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].call, "Config.objects.filter(active=True).update")

    def test_safe_handler_on_commit_is_rejected(self):
        violations = self._violations(
            "class View:\n"
            "    def retrieve(self, request):\n"
            "        transaction.on_commit(lambda: notify())\n"
        )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].call, "transaction.on_commit")

    def test_safe_handler_model_save_is_rejected(self):
        violations = self._violations(
            "class View:\n"
            "    def get(self, request):\n"
            "        config = Config.objects.get(tenant=request.tenant)\n"
            "        config.seen = True\n"
            "        config.save(update_fields=['seen'])\n"
        )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].call, "config.save")

    def test_post_write_and_get_read_are_allowed(self):
        violations = self._violations(
            "class View:\n"
            "    def post(self, request):\n"
            "        return Config.objects.create(tenant=request.tenant)\n"
            "    def get(self, request):\n"
            "        return Config.objects.filter(tenant=request.tenant).first()\n"
        )

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
