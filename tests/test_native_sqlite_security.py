"""Fail closed on SQLite runtime/ABI drift; do not run exploit fixtures."""
import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("verify_sqlite", ROOT / "docker/native-security/verify-sqlite.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def baseline():
    return {"version": "3.46.1", "source_id": "old", "soname": "libsqlite3.so.0",
            "symbols": ["sqlite3_open", "sqlite3_close", "sqlite3session_create"],
            "public_symbols": ["sqlite3_open", "sqlite3_close", "sqlite3session_create"], "public_header_sha256": "test-header-hash",
            "private_source_sha3": verifier.BASELINE_SOURCE_SHA3,
            "options": ["COMPILER=gcc-14.2.0", "ENABLE_FTS5", "ENABLE_SESSION", "THREADSAFE=1", "MAX_VARIABLE_NUMBER=250000", "DEFAULT_CACHE_SIZE=-2000"]}


def current():
    result = copy.deepcopy(baseline())
    result.update(version=verifier.VERSION, source_id=verifier.SOURCE_ID)
    result["symbols"].append("sqlite3_incomplete")
    result["options"][0] = "COMPILER=gcc-14.2.1"
    return result


def test_fixed_version_keeps_existing_abi_and_features_with_additions():
    verifier.verify_compatibility(baseline(), current())
    flags = verifier.compiler_flags(baseline())
    assert "-DSQLITE_ENABLE_FTS5" in flags and "-DSQLITE_ENABLE_SESSION" in flags
    assert "-DSQLITE_MAX_VARIABLE_NUMBER=250000" in flags
    assert "-DSQLITE_DEFAULT_CACHE_SIZE=-2000" in flags
    assert not any("COMPILER" in flag for flag in flags)


HEADER = b'''#define SQLITE_VERSION "3.46.1"
#define SQLITE_API
/* SQLITE_API int sqlite3Internal(void); is only a comment. */
SQLITE_API SQLITE_EXTERN const char sqlite3_version[];
SQLITE_API int sqlite3_open(const char*, sqlite3**);
SQLITE_API int sqlite3_close(sqlite3*);
SQLITE_API SQLITE_DEPRECATED int sqlite3session_create(sqlite3*,const char*,sqlite3_session**);
'''
PRIVATE = b"SQLITE_PRIVATE int sqlite3Internal(void);"


def test_public_abi_classification_uses_actual_header_and_private_declarations(monkeypatch):
    monkeypatch.setattr(verifier, "BASELINE_SOURCE_SHA3", hashlib.sha3_256(PRIVATE).hexdigest())
    old = baseline()
    old["symbols"].extend(["sqlite3_version", "sqlite3Internal"])
    verifier.classify_public_api(old, HEADER, PRIVATE)
    assert old["non_header_symbols"] == ["sqlite3Internal"]
    assert "sqlite3_version" in old["public_symbols"]
    candidate = current()
    candidate["symbols"].append("sqlite3_version")
    verifier.verify_compatibility(old, candidate)


def test_unknown_public_name_requires_review_instead_of_prefix_filtering(monkeypatch):
    monkeypatch.setattr(verifier, "BASELINE_SOURCE_SHA3", hashlib.sha3_256(PRIVATE).hexdigest())
    old = baseline()
    old["symbols"].append("sqlite3_unknown_public")
    with pytest.raises(RuntimeError, match="UNCLASSIFIED_EXPORT: sqlite3_unknown_public"):
        verifier.classify_public_api(old, HEADER, PRIVATE)


def test_header_version_and_public_abi_evidence_are_required():
    with pytest.raises(RuntimeError, match="HEADER_VERSION"):
        verifier.classify_public_api(baseline(), HEADER.replace(b"3.46.1", b"3.45.0"), PRIVATE)
    old = baseline()
    old.pop("public_header_sha256")
    with pytest.raises(RuntimeError, match="CLASSIFICATION_REQUIRED"):
        verifier.verify_compatibility(old, current())


def test_private_classification_requires_sealed_original_source():
    with pytest.raises(RuntimeError, match="PRIVATE_SOURCE_HASH"):
        verifier.classify_public_api(baseline(), HEADER, PRIVATE)


def test_canonical_source_rejects_unsealed_archive_and_wrong_release(tmp_path, monkeypatch):
    archive = tmp_path / "source.zip"
    archive.write_bytes(b"sealed canonical source fixture")
    monkeypatch.setattr(verifier, "ARCHIVE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(verifier, "ARCHIVE_SHA3", hashlib.sha3_256(archive.read_bytes()).hexdigest())
    (tmp_path / "VERSION").write_text(verifier.VERSION)
    (tmp_path / "manifest.uuid").write_text(verifier.SOURCE_ID.split()[-1])
    verifier.verify_source(tmp_path, archive)
    (tmp_path / "manifest.uuid").write_text("another revision")
    with pytest.raises(RuntimeError, match="UPSTREAM_SOURCE_ID"):
        verifier.verify_source(tmp_path, archive)
    archive.write_bytes(b"modified archive")
    with pytest.raises(RuntimeError, match="UPSTREAM_ARCHIVE_HASH"):
        verifier.verify_source(tmp_path, archive)


def test_regenerated_source_records_hash_and_rejects_header_drift(tmp_path):
    source = b"generated parser with baseline options"
    (tmp_path / "sqlite3.c").write_bytes(source)
    header = f'#define SQLITE_VERSION "{verifier.VERSION}"\n#define SQLITE_SOURCE_ID "{verifier.SOURCE_ID}"\n'
    (tmp_path / "sqlite3.h").write_text(header)
    assert verifier.generated_source_hash(tmp_path) == hashlib.sha256(source).hexdigest()
    (tmp_path / "sqlite3.h").write_text(header.replace(verifier.SOURCE_ID, "changed"))
    with pytest.raises(RuntimeError, match="HEADER_SOURCE"):
        verifier.generated_source_hash(tmp_path)


@pytest.mark.parametrize("field,value,error", [
    ("version", "3.46.1", "VERSION"), ("source_id", "relabelled", "SOURCE"),
    ("soname", "libsqlite3.so.1", "SONAME"),
    ("symbols", ["sqlite3_open", "sqlite3_close"], "SYMBOL"),
    ("options", ["ENABLE_SESSION", "THREADSAFE=1"], "OPTION"),
    ("options", ["ENABLE_FTS5", "ENABLE_SESSION", "THREADSAFE=0", "MAX_VARIABLE_NUMBER=250000"], "OPTION"),
])
def test_runtime_contract_rejects_old_relabelled_or_incompatible_libraries(field, value, error):
    candidate = current()
    candidate[field] = value
    with pytest.raises(RuntimeError, match=error):
        verifier.verify_compatibility(baseline(), candidate)


@pytest.mark.parametrize("extra", ["DANGER=$(touch file)", "SETTING=1 -UOTHER", "SETTING='quoted'", "ENABLE_FTS5;echo"])
def test_compile_options_cannot_inject_shell_or_compiler_arguments(extra):
    old = baseline()
    old["options"].append(extra)
    with pytest.raises(RuntimeError, match="UNSUPPORTED_BASELINE_MACRO"):
        verifier.compiler_flags(old)


@pytest.mark.parametrize("missing", ["ENABLE_FTS5", "THREADSAFE=1"])
def test_capture_refuses_a_reduced_baseline(missing):
    old = baseline()
    old["options"].remove(missing)
    with pytest.raises(RuntimeError, match="BASELINE"):
        verifier.compiler_flags(old)


@pytest.mark.parametrize("key,value", [("Version", "3.46.1-7+deb13u2"), ("Source", "hidden-sqlite"), ("db:Status-Status", "config-files")])
def test_package_identity_must_be_installed_and_truthful(monkeypatch, key, value):
    fields = {"Version": verifier.PACKAGE_VERSION, "Source": "sqlite3", "db:Status-Status": "installed"}
    fields[key] = value
    def query(argv, *, text):
        assert argv[:2] == ["dpkg-query", "-W"]
        return fields[argv[2][5:-1]]
    monkeypatch.setattr(verifier.subprocess, "check_output", query)
    with pytest.raises(RuntimeError, match="PACKAGE"):
        verifier.verify_package()


@pytest.mark.parametrize("cli", [False, True])
def test_package_contract_rejects_runtime_cli(monkeypatch, cli):
    def query(argv, *, text):
        if argv[1] == "-L":
            return "/usr/lib/x86_64-linux-gnu/libsqlite3.so.0\n"
        return {"-f=${Version}": verifier.PACKAGE_VERSION, "-f=${Source}": "sqlite3",
                "-f=${db:Status-Status}": "installed"}[argv[2]]
    monkeypatch.setattr(verifier.subprocess, "check_output", query)
    monkeypatch.setattr(verifier.shutil, "which", lambda _: "/usr/bin/sqlite3" if cli else None)
    if cli:
        with pytest.raises(RuntimeError, match="CLI_PRESENT"):
            verifier.verify_package()
    else:
        verifier.verify_package()


def test_all_six_runtime_images_recheck_after_their_package_install():
    invocation = "python /usr/local/bin/verify-sqlite.py --package"
    for name in ["Dockerfile.base", "api/Dockerfile", "ai-worker-cpu/Dockerfile",
                 "tools-worker/Dockerfile", "video-worker/Dockerfile", "messaging-worker/Dockerfile"]:
        text = (ROOT / "docker" / name).read_text(encoding="utf-8")
        boundary = text.rindex("pip install") if name.startswith("messaging") else text.rindex("apt-get install")
        assert text.rindex(invocation) > boundary, name
