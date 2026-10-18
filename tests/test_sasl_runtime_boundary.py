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
        rows = [["libsasl2-2", "cyrus-sasl2", "2.1.28+dfsg1-12"],
                ["libsasl2-modules-db", "cyrus-sasl2", "2.1.28+dfsg1-12"]]
        self.assertEqual(runtime.check_packages(rows), rows)
        runtime.check_mechanisms(["EXTERNAL"], -4)

    def test_old_or_mixed_package_versions_are_rejected(self):
        for old_package in ("libsasl2-2", "libsasl2-modules-db"):
            rows = [[name, "cyrus-sasl2", "2.1.28+dfsg1-9" if name == old_package
                     else "2.1.28+dfsg1-12"]
                    for name in ("libsasl2-2", "libsasl2-modules-db")]
            with self.subTest(package=old_package), self.assertRaisesRegex(RuntimeError, "fixed version"):
                runtime.check_packages(rows)

    def test_missing_core_or_database_package_is_not_a_fixed_runtime(self):
        for rows in ([], [["libsasl2-2", "cyrus-sasl2", "2.1.28+dfsg1-12"]],
                     [["libsasl2-modules-db", "cyrus-sasl2", "2.1.28+dfsg1-12"]]):
            with self.subTest(rows=rows), self.assertRaisesRegex(RuntimeError, "package pair"):
                runtime.check_packages(rows)

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
