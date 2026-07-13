from io import StringIO

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.billing.admin import BillingKeyAdmin
from apps.billing.models import BillingKey, BillingProfile
from apps.billing.services.billing_key_crypto import (
    BillingKeyConfigurationError,
    BillingKeyDecryptionError,
    decrypt_billing_key,
    encrypt_billing_key,
    reencrypt_billing_key,
)
from apps.core.models import Tenant

CURRENT_KEK = Fernet.generate_key().decode("ascii")
OLD_KEK = Fernet.generate_key().decode("ascii")
NEW_KEK = Fernet.generate_key().decode("ascii")


class BillingKeyCryptoTests(SimpleTestCase):
    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=CURRENT_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
    )
    def test_round_trip_never_contains_plaintext(self):
        encrypted = encrypt_billing_key("billing-secret-token")

        self.assertTrue(encrypted.startswith("enc:v1:"))
        self.assertNotIn("billing-secret-token", encrypted)
        self.assertEqual(decrypt_billing_key(encrypted), "billing-secret-token")

    @override_settings(BILLING_KEY_ENCRYPTION_WRITE_ENABLED=False)
    def test_phase_a_write_keeps_rolling_plaintext_compatibility(self):
        self.assertEqual(encrypt_billing_key("legacy-token"), "legacy-token")
        self.assertEqual(decrypt_billing_key("legacy-token"), "legacy-token")

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=NEW_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(OLD_KEK,),
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
    )
    def test_keyring_fallback_decrypts_pre_rotation_ciphertext(self):
        with override_settings(
            BILLING_KEY_ENCRYPTION_PRIMARY_KEY=OLD_KEK,
            BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
        ):
            encrypted = encrypt_billing_key("rotating-token")

        self.assertEqual(decrypt_billing_key(encrypted), "rotating-token")

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=CURRENT_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
    )
    def test_invalid_ciphertext_fails_closed(self):
        with self.assertRaises(BillingKeyDecryptionError):
            decrypt_billing_key("enc:v1:not-a-valid-token")

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=NEW_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(OLD_KEK,),
    )
    def test_reencrypt_wraps_with_current_secret(self):
        with override_settings(
            BILLING_KEY_ENCRYPTION_PRIMARY_KEY=OLD_KEK,
            BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
            BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
        ):
            old_ciphertext = encrypt_billing_key("rotating-token")

        new_ciphertext = reencrypt_billing_key(old_ciphertext)

        self.assertNotEqual(new_ciphertext, old_ciphertext)
        with override_settings(BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=()):
            self.assertEqual(decrypt_billing_key(new_ciphertext), "rotating-token")

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=OLD_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(NEW_KEK,),
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
    )
    def test_first_rotation_refresh_can_read_future_primary_ciphertext(self):
        with override_settings(
            BILLING_KEY_ENCRYPTION_PRIMARY_KEY=NEW_KEK,
            BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(OLD_KEK,),
        ):
            future_ciphertext = encrypt_billing_key("future-writer-token")

        self.assertEqual(
            decrypt_billing_key(future_ciphertext),
            "future-writer-token",
        )

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=OLD_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=f"{CURRENT_KEK},{NEW_KEK}",
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
    )
    def test_comma_serialized_environment_fallback_keyring_is_supported(self):
        with override_settings(
            BILLING_KEY_ENCRYPTION_PRIMARY_KEY=NEW_KEK,
            BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
        ):
            ciphertext = encrypt_billing_key("serialized-fallback-token")

        self.assertEqual(
            decrypt_billing_key(ciphertext),
            "serialized-fallback-token",
        )

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY="",
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
    )
    def test_missing_dedicated_keyring_fails_closed(self):
        with self.assertRaises(BillingKeyConfigurationError):
            encrypt_billing_key("must-not-fall-back-to-django-secret")


@override_settings(
    BILLING_KEY_ENCRYPTION_PRIMARY_KEY=NEW_KEK,
    BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(OLD_KEK,),
    BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
)
class BillingKeyRotationCommandTests(TestCase):
    def setUp(self):
        tenant = Tenant.objects.create(code="crypto-rotate", name="Crypto Rotate")
        profile = BillingProfile.objects.create(tenant=tenant)
        with override_settings(
            BILLING_KEY_ENCRYPTION_PRIMARY_KEY=OLD_KEK,
            BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
            BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
        ):
            old_ciphertext = encrypt_billing_key("provider-key-never-log")
        self.billing_key = BillingKey.objects.create(
            tenant=tenant,
            billing_profile=profile,
            billing_key=old_ciphertext,
        )

    def test_default_is_read_only_and_never_prints_secret(self):
        output = StringIO()

        call_command("rotate_billing_key_encryption", stdout=output)

        self.billing_key.refresh_from_db()
        self.assertIn("would_reencrypt=1", output.getvalue())
        self.assertNotIn("provider-key-never-log", output.getvalue())

    def test_execute_rewraps_for_current_secret(self):
        call_command(
            "rotate_billing_key_encryption",
            execute=True,
            confirm_live=True,
        )

        self.billing_key.refresh_from_db()
        with override_settings(BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=()):
            self.assertEqual(
                decrypt_billing_key(self.billing_key.billing_key),
                "provider-key-never-log",
            )

    def test_strict_audit_rejects_undecryptable_ciphertext_without_leaking_it(self):
        BillingKey.objects.filter(pk=self.billing_key.pk).update(
            billing_key="enc:v1:not-valid-or-sensitive"
        )
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "billing_audit_strict_failed"):
            call_command(
                "audit_billing_fields",
                tenant="crypto-rotate",
                strict=True,
                stdout=output,
            )

        audit = output.getvalue()
        self.assertIn(
            f"undecryptable_billing_key id={self.billing_key.id}",
            audit,
        )
        self.assertNotIn("not-valid-or-sensitive", audit)

    def test_model_writer_encrypts_and_admin_never_exposes_secret_field(self):
        tenant = Tenant.objects.create(code="crypto-direct", name="Crypto Direct")
        profile = BillingProfile.objects.create(tenant=tenant)

        direct = BillingKey.objects.create(
            tenant=tenant,
            billing_profile=profile,
            billing_key="direct-model-provider-key",
        )

        self.assertTrue(direct.billing_key.startswith("enc:v1:"))
        self.assertNotIn("direct-model-provider-key", direct.billing_key)
        self.assertIn("billing_key", BillingKeyAdmin.exclude)
