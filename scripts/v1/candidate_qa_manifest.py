"""Controller compatibility entry for the shared immutable QA root contract."""
from apps.infrastructure import qa_resource_manifest as _core
from scripts.v1.candidate_qa_window import WindowHold

SCHEMA = _core.SCHEMA
MAX_BYTES = _core.MAX_BYTES
RootManifest = _core.RootManifest
canonical_bytes = _core.canonical_bytes
fingerprint = _core.fingerprint
owned_prefixes = _core.owned_prefixes


def validate_root(value, *, expected_bucket):
    try:
        return _core.validate_root(value, expected_bucket=expected_bucket)
    except _core.ManifestError as exc:
        raise WindowHold(str(exc)) from None


def encode_root(value, *, expected_bucket):
    try:
        return _core.encode_root(value, expected_bucket=expected_bucket)
    except _core.ManifestError as exc:
        raise WindowHold(str(exc)) from None


def load_root(path, record, *, expected_bucket):
    try:
        return _core.load_root(path, record, expected_bucket=expected_bucket)
    except _core.ManifestError as exc:
        raise WindowHold(str(exc)) from None
