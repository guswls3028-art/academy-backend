import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "verify_sasl_runtime", Path(__file__).resolve().parents[1] / "docker/native-security/verify-sasl.py",
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class SaslRuntimeBoundaryTests(unittest.TestCase):
    def test_core_and_database_plugin_remain_supported(self):
        rows = [["libsasl2-2", "cyrus-sasl2", "2.1.28+dfsg1-9"],
                ["libsasl2-modules-db", "cyrus-sasl2", "2.1.28+dfsg1-9"]]
        self.assertEqual(runtime.check_packages(rows), rows)
        runtime.check_mechanisms(["EXTERNAL"], -4)

    def test_authentication_plugin_package_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "plugin packages"):
            runtime.check_packages([["libsasl2-modules", "cyrus-sasl2", "2.1.28+dfsg1-9"]])

    def test_renamed_or_unmanaged_digest_plugin_is_rejected_by_mechanism(self):
        with self.assertRaisesRegex(RuntimeError, "mechanism list"):
            runtime.check_mechanisms(["EXTERNAL", "digest-md5"], -4)

    def test_initialization_or_callback_errors_are_not_absence_proof(self):
        for code in (-1, -2, 0, 1, 2):
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, "SASL_NOMECH"):
                runtime.check_mechanisms(["EXTERNAL"], code)
