from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

ENCRYPTED_PREFIX = "enc:v1:"


class BillingKeyCryptoError(ValueError):
    """Base class for local billing credential protection failures."""


class BillingKeyConfigurationError(BillingKeyCryptoError):
    """The dedicated billing credential keyring is missing or invalid."""


class BillingKeyDecryptionError(BillingKeyCryptoError):
    """Stored billing credential cannot be decrypted by configured keys."""


def _fernet_for_key(key: str) -> Fernet:
    try:
        return Fernet(str(key).encode("ascii"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise BillingKeyConfigurationError(
            "billing_key_encryption_key_invalid"
        ) from exc


def _configured_fernets() -> list[Fernet]:
    primary = str(
        getattr(settings, "BILLING_KEY_ENCRYPTION_PRIMARY_KEY", "") or ""
    ).strip()
    fallbacks = getattr(settings, "BILLING_KEY_ENCRYPTION_FALLBACK_KEYS", ())
    if isinstance(fallbacks, str):
        fallbacks = tuple(
            value.strip() for value in fallbacks.split(",") if value.strip()
        )
    keys = [primary, *(str(key).strip() for key in fallbacks)]
    configured = [_fernet_for_key(key) for key in keys if key]
    if not configured:
        raise BillingKeyConfigurationError(
            "billing_key_encryption_keyring_missing"
        )
    return configured


def encrypt_billing_key(value: str) -> str:
    plaintext = str(value or "")
    if not plaintext or plaintext.startswith(ENCRYPTED_PREFIX):
        return plaintext
    if not getattr(settings, "BILLING_KEY_ENCRYPTION_WRITE_ENABLED", False):
        # Rolling Phase A: new binaries can read ciphertext before any writer
        # begins producing it. Enable only after the compatible fleet is live.
        return plaintext
    token = _configured_fernets()[0].encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_PREFIX}{token}"


def reencrypt_billing_key(value: str) -> str:
    """Decrypt with current/fallback secrets and wrap with the current secret."""

    plaintext = decrypt_billing_key(value)
    if not plaintext:
        return plaintext
    token = _configured_fernets()[0].encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_billing_key(value: str) -> str:
    stored = str(value or "")
    if not stored.startswith(ENCRYPTED_PREFIX):
        return stored
    token = stored[len(ENCRYPTED_PREFIX):].encode("ascii")
    for fernet in _configured_fernets():
        try:
            return fernet.decrypt(token).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError):
            continue
    raise BillingKeyDecryptionError("stored_billing_key_decryption_failed")
