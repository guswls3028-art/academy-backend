"""SQLite package/ABI/normal-use contract. No malformed database or PoC inputs."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sysconfig


VERSION = "3.54.1"
PACKAGE_VERSION = "3.54.1-1+academy1"
SOURCE_ID = "2026-10-16 14:45:20 555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76"
ARCHIVE_SHA3 = "877c6f4996ce7adb368686bce21638de9e3d91534832ad3d9d119037d1bc22ad"
ARCHIVE_SHA256 = "387e7f6cad4797b17848c07d453dc6606917bb356a6cfe5a3abafd38c240c71b"
BASELINE_SOURCE_SHA3 = "186a1baa476b6d546de155160ca6d30ff7b7e6ee375f0bb6445e1a3d180a7dad"
SONAME = "libsqlite3.so.0"
RECORD_ROOT = Path("/usr/share/academy/native-security")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def bind(lib, name, args, result):
    function = getattr(lib, name)
    function.argtypes, function.restype = args, result
    return function


def library_info(lib):
    version = bind(lib, "sqlite3_libversion", [], ctypes.c_char_p)().decode()
    source_id = bind(lib, "sqlite3_sourceid", [], ctypes.c_char_p)().decode()
    option_get = bind(lib, "sqlite3_compileoption_get", [ctypes.c_int], ctypes.c_char_p)
    options = []
    for index in range(1000):
        value = option_get(index)
        if value is None:
            return {"version": version, "source_id": source_id, "options": sorted(options)}
        options.append(value.decode())
    raise RuntimeError("SQLITE_COMPILE_OPTIONS_UNBOUNDED")


def stable_options(options):
    return {option for option in options if not option.startswith("COMPILER=")}


def declared_api(header):
    """Read declarations, including exported variables, from the vendor header."""
    text = re.sub(r"/\*.*?\*/", "", header, flags=re.S)
    text = re.sub(r"^\s*#.*$", "", text, flags=re.M)
    names = set()
    for declaration in re.findall(r"\bSQLITE_API\s+([^;]+);", text):
        match = re.search(r"\b(sqlite3[A-Za-z0-9_]*)\s*(?=\(|\[|$)", declaration.strip())
        require(match is not None, "SQLITE_UNPARSED_PUBLIC_DECLARATION")
        names.add(match[1])
    require({"sqlite3_open", "sqlite3_close", "sqlite3_version"} <= names, "SQLITE_PUBLIC_HEADER_INCOMPLETE")
    return names


def classify_public_api(baseline, header_bytes, private_source):
    header = header_bytes.decode("utf-8")
    require(re.search(r'#define SQLITE_VERSION\s+"' + re.escape(baseline["version"]) + '"', header),
            "SQLITE_BASELINE_HEADER_VERSION_MISMATCH")
    declared = declared_api(header)
    exported = set(baseline["symbols"])
    require(hashlib.sha3_256(private_source).hexdigest() == BASELINE_SOURCE_SHA3,
            "SQLITE_BASELINE_PRIVATE_SOURCE_HASH_MISMATCH")
    text = re.sub(r"/\*.*?\*/", "", private_source.decode("utf-8"), flags=re.S)
    private = set()
    for declaration in re.findall(r"\bSQLITE_PRIVATE\s+([^;{}]+)(?:[;{])", text):
        match = re.search(r"\b(sqlite3[A-Za-z0-9_]*)\s*(?=\(|\[|=|$)", declaration.strip())
        if match:
            private.add(match[1])
    # Every non-header export needs an actual SQLITE_PRIVATE declaration from
    # the pinned original source. Never classify or discard by spelling alone.
    unknown = sorted(exported - declared - private)
    require(not unknown, "SQLITE_UNCLASSIFIED_EXPORT: " + ",".join(unknown))
    baseline["public_symbols"] = sorted(exported & declared)
    baseline["non_header_symbols"] = sorted(exported - declared)
    baseline["public_header_sha256"] = hashlib.sha256(header_bytes).hexdigest()
    baseline["private_source_sha3"] = BASELINE_SOURCE_SHA3


def compiler_flags(baseline):
    options = stable_options(baseline["options"])
    require("ENABLE_FTS5" in options, "SQLITE_BASELINE_FTS5_MISSING")
    require("THREADSAFE=1" in options, "SQLITE_BASELINE_THREADING_CHANGED")
    for option in options:
        require(re.fullmatch(r"[A-Z][A-Z0-9_]*(?:=-?(?:[0-9]+|0x[0-9a-fA-F]+))?", option),
                "SQLITE_UNSUPPORTED_BASELINE_MACRO")
    return ["-DSQLITE_" + option for option in sorted(options)]


def verify_compatibility(baseline, current):
    require(current["version"] == VERSION, "SQLITE_RUNTIME_VERSION_MISMATCH")
    require(current["source_id"] == SOURCE_ID, "SQLITE_RUNTIME_SOURCE_MISMATCH")
    require(baseline["soname"] == current["soname"] == SONAME, "SQLITE_SONAME_CHANGED")
    require(bool(baseline.get("public_header_sha256")) and bool(baseline.get("public_symbols")),
            "SQLITE_PUBLIC_ABI_CLASSIFICATION_REQUIRED")
    require(baseline.get("private_source_sha3") == BASELINE_SOURCE_SHA3,
            "SQLITE_PRIVATE_ABI_CLASSIFICATION_REQUIRED")
    missing_symbols = sorted(set(baseline["public_symbols"]) - set(current["symbols"]))
    require(not missing_symbols, "SQLITE_PUBLIC_SYMBOL_REMOVED: " + ",".join(missing_symbols))
    missing_options = sorted(stable_options(baseline["options"]) - stable_options(current["options"]))
    require(not missing_options, "SQLITE_COMPILE_OPTION_REMOVED_OR_CHANGED: " + ",".join(missing_options))
    require("ENABLE_FTS5" in current["options"], "SQLITE_FTS5_MISSING")


def snapshot(path):
    path = Path(path).resolve(strict=True)
    lib = ctypes.CDLL(str(path))
    result = library_info(lib)
    dynamic = subprocess.check_output(["readelf", "-d", str(path)], text=True)
    names = re.findall(r"\(SONAME\).*\[([^]]+)\]", dynamic)
    require(names == [SONAME], "SQLITE_SONAME_MISMATCH")
    output = subprocess.check_output(["nm", "-D", "--defined-only", str(path)], text=True)
    symbols = sorted({line.split()[-1].split("@", 1)[0] for line in output.splitlines() if line.split()})
    require("sqlite3_open" in symbols and "sqlite3_close" in symbols, "SQLITE_SYMBOL_SNAPSHOT_EMPTY")
    result.update(soname=names[0], symbols=symbols, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def benign_sql(lib):
    """Ordinary in-memory transactions, FTS5 and a generated valid changeset."""
    pointer = ctypes.c_void_p
    open_db = bind(lib, "sqlite3_open", [ctypes.c_char_p, ctypes.POINTER(pointer)], ctypes.c_int)
    close_db = bind(lib, "sqlite3_close", [pointer], ctypes.c_int)
    execute = bind(lib, "sqlite3_exec", [pointer, ctypes.c_char_p, pointer, pointer, pointer], ctypes.c_int)
    db, other = pointer(), pointer()
    require(open_db(b":memory:", ctypes.byref(db)) == 0, "SQLITE_OPEN_FAILED")
    try:
        require(execute(db, b"CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT);"
                        b"BEGIN; INSERT INTO records VALUES(1,'academy'); ROLLBACK;"
                        b"CREATE VIRTUAL TABLE texts USING fts5(body);"
                        b"INSERT INTO texts VALUES('academy');", None, None, None) == 0,
                "SQLITE_NORMAL_SQL_FAILED")
        rows = []
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, pointer, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p))
        @callback_type
        def collect(_context, count, values, _names):
            rows.append(tuple(values[i] for i in range(count)))
            return 0
        require(execute(db, b"SELECT body FROM texts WHERE texts MATCH 'academy'",
                        ctypes.cast(collect, pointer), None, None) == 0 and rows == [(b"academy",)],
                "SQLITE_FTS5_QUERY_FAILED")
        options = library_info(lib)["options"]
        if "ENABLE_UPDATE_DELETE_LIMIT" in options:
            require(execute(db, b"CREATE TABLE limits(id INTEGER PRIMARY KEY, value TEXT);"
                            b"INSERT INTO limits VALUES(1,'one'),(2,'two');"
                            b"UPDATE limits SET value='changed' ORDER BY id LIMIT 1;"
                            b"DELETE FROM limits ORDER BY id DESC LIMIT 1;", None, None, None) == 0,
                    "SQLITE_UPDATE_DELETE_LIMIT_PARSER_MISSING")
            rows.clear()
            require(execute(db, b"SELECT id,value FROM limits", ctypes.cast(collect, pointer), None, None) == 0
                    and rows == [(b"1", b"changed")], "SQLITE_UPDATE_DELETE_LIMIT_RESULT_MISMATCH")
        if "ALLOW_ROWID_IN_VIEW" in options:
            require(execute(db, b"CREATE VIEW record_view AS SELECT id FROM records;"
                            b"SELECT rowid FROM record_view;", None, None, None) == 0,
                    "SQLITE_ROWID_VIEW_COMPATIBILITY_MISSING")
        if "ENABLE_SESSION" in options:
            session, changes = pointer(), pointer()
            size = ctypes.c_int()
            create = bind(lib, "sqlite3session_create", [pointer, ctypes.c_char_p, ctypes.POINTER(pointer)], ctypes.c_int)
            attach = bind(lib, "sqlite3session_attach", [pointer, ctypes.c_char_p], ctypes.c_int)
            changeset = bind(lib, "sqlite3session_changeset", [pointer, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(pointer)], ctypes.c_int)
            delete = bind(lib, "sqlite3session_delete", [pointer], None)
            free = bind(lib, "sqlite3_free", [pointer], None)
            apply = bind(lib, "sqlite3changeset_apply", [pointer, ctypes.c_int, pointer, pointer, pointer, pointer], ctypes.c_int)
            conflict_type = ctypes.CFUNCTYPE(ctypes.c_int, pointer, ctypes.c_int, pointer)
            @conflict_type
            def abort_conflict(_context, _reason, _iterator):
                return 2  # SQLITE_CHANGESET_ABORT; a valid empty target has no conflict.
            require(create(db, b"main", ctypes.byref(session)) == 0, "SQLITE_SESSION_CREATE_FAILED")
            try:
                require(attach(session, b"records") == 0, "SQLITE_SESSION_ATTACH_FAILED")
                require(execute(db, b"INSERT INTO records VALUES(1,'academy')", None, None, None) == 0,
                        "SQLITE_SESSION_INSERT_FAILED")
                require(changeset(session, ctypes.byref(size), ctypes.byref(changes)) == 0 and size.value > 0,
                        "SQLITE_SESSION_CHANGESET_FAILED")
                require(open_db(b":memory:", ctypes.byref(other)) == 0, "SQLITE_SESSION_TARGET_OPEN_FAILED")
                require(execute(other, b"CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)", None, None, None) == 0,
                        "SQLITE_SESSION_TARGET_SCHEMA_FAILED")
                require(apply(other, size, changes, None, ctypes.cast(abort_conflict, pointer), None) == 0,
                        "SQLITE_SESSION_APPLY_FAILED")
                rows.clear()
                require(execute(other, b"SELECT value FROM records WHERE id=1", ctypes.cast(collect, pointer), None, None) == 0
                        and rows == [(b"academy",)], "SQLITE_SESSION_RESULT_MISMATCH")
            finally:
                if changes.value:
                    free(changes)
                delete(session)
    finally:
        if other.value:
            require(close_db(other) == 0, "SQLITE_TARGET_CLOSE_FAILED")
        require(close_db(db) == 0, "SQLITE_CLOSE_FAILED")


def verify_source(root, archive):
    root = Path(root)
    data = Path(archive).read_bytes()
    require(hashlib.sha256(data).hexdigest() == ARCHIVE_SHA256
            and hashlib.sha3_256(data).hexdigest() == ARCHIVE_SHA3, "SQLITE_UPSTREAM_ARCHIVE_HASH_MISMATCH")
    require((root / "VERSION").read_text().strip() == VERSION, "SQLITE_UPSTREAM_VERSION_MISMATCH")
    require((root / "manifest.uuid").read_text().strip() == SOURCE_ID.split()[-1], "SQLITE_UPSTREAM_SOURCE_ID_MISMATCH")


def generated_source_hash(root):
    root = Path(root)
    header = (root / "sqlite3.h").read_text()
    require(re.search(r'#define SQLITE_VERSION\s+"' + re.escape(VERSION) + '"', header), "SQLITE_HEADER_VERSION_MISMATCH")
    require(SOURCE_ID in header, "SQLITE_HEADER_SOURCE_MISMATCH")
    return hashlib.sha256((root / "sqlite3.c").read_bytes()).hexdigest()


def verify_package():
    for key, expected in (("Version", PACKAGE_VERSION), ("Source", "sqlite3"), ("db:Status-Status", "installed")):
        actual = subprocess.check_output(["dpkg-query", "-W", f"-f=${{{key}}}", "libsqlite3-0"], text=True)
        require(actual == expected, "SQLITE_PACKAGE_" + key + "_MISMATCH")
    files = subprocess.check_output(["dpkg-query", "-L", "libsqlite3-0"], text=True).splitlines()
    require(not any(Path(path).name in {"sqlite", "sqlite3"} for path in files), "SQLITE_CLI_PACKAGED")
    require(shutil.which("sqlite3") is None and shutil.which("sqlite") is None, "SQLITE_CLI_PRESENT")


def verify_runtime():
    import _sqlite3
    import sqlite3

    verify_package()
    baseline = json.loads((RECORD_ROOT / "sqlite-baseline.json").read_text())
    built = json.loads((RECORD_ROOT / "sqlite-build.json").read_text())
    path = (Path("/usr/lib") / sysconfig.get_config_var("MULTIARCH") / SONAME).resolve(strict=True)
    require(hashlib.sha256(path.read_bytes()).hexdigest() == built["sha256"], "SQLITE_RUNTIME_LIBRARY_HASH_MISMATCH")
    require(built["archive_sha256"] == ARCHIVE_SHA256, "SQLITE_BUILD_PROVENANCE_MISMATCH")
    require(built.get("archive_sha3") == ARCHIVE_SHA3
            and re.fullmatch(r"[0-9a-f]{64}", built.get("generated_source_sha256", "")), "SQLITE_GENERATED_SOURCE_PROVENANCE_MISMATCH")
    lib = ctypes.CDLL(str(path))
    current = library_info(lib)
    current.update(soname=built["soname"], symbols=[name for name in baseline["public_symbols"] if hasattr(lib, name)])
    verify_compatibility(baseline, current)
    python_lib = ctypes.CDLL(_sqlite3.__file__)
    require(ctypes.cast(python_lib.sqlite3_libversion, ctypes.c_void_p).value
            == ctypes.cast(lib.sqlite3_libversion, ctypes.c_void_p).value, "SQLITE_PYTHON_LIBRARY_BINDING_MISMATCH")
    require(sqlite3.sqlite_version == VERSION, "SQLITE_PYTHON_VERSION_MISMATCH")
    with sqlite3.connect(":memory:") as db:
        require(db.execute("select sqlite_source_id()").fetchone() == (SOURCE_ID,), "SQLITE_PYTHON_SOURCE_MISMATCH")
        db.execute("create virtual table texts using fts5(body)")
        db.execute("insert into texts values (?)", ("academy",))
        require(db.execute("select body from texts where texts match ?", ("academy",)).fetchall() == [("academy",)],
                "SQLITE_PYTHON_FTS5_FAILED")
    benign_sql(lib)
    print("SQLITE_FIXED_PACKAGE_ABI_PYTHON_FTS5_SESSION_PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture-baseline")
    group.add_argument("--compiler-flags")
    group.add_argument("--source")
    group.add_argument("--verify-build")
    group.add_argument("--package", action="store_true")
    parser.add_argument("--baseline")
    parser.add_argument("--public-header")
    parser.add_argument("--private-source")
    parser.add_argument("--archive")
    parser.add_argument("--source-root")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.package:
        verify_runtime()
    elif args.compiler_flags:
        print(" ".join(compiler_flags(json.loads(Path(args.compiler_flags).read_text()))))
    elif args.source:
        require(args.archive is not None, "SQLITE_SOURCE_ARCHIVE_REQUIRED")
        verify_source(args.source, args.archive)
    else:
        result = snapshot(args.capture_baseline or args.verify_build)
        if args.capture_baseline:
            require(result["version"] == "3.46.1", "SQLITE_BASELINE_VERSION_CHANGED")
            require(args.public_header is not None, "SQLITE_BASELINE_HEADER_REQUIRED")
            require(args.private_source is not None, "SQLITE_BASELINE_PRIVATE_SOURCE_REQUIRED")
            classify_public_api(result, Path(args.public_header).read_bytes(), Path(args.private_source).read_bytes())
            compiler_flags(result)
        else:
            baseline = json.loads(Path(args.baseline).read_text())
            verify_compatibility(baseline, result)
            benign_sql(ctypes.CDLL(str(Path(args.verify_build).resolve())))
            result["removed_non_header_exports"] = sorted(set(baseline["non_header_symbols"]) - set(result["symbols"]))
            result["archive_sha256"] = ARCHIVE_SHA256
            result["archive_sha3"] = ARCHIVE_SHA3
            require(args.source_root is not None, "SQLITE_GENERATED_SOURCE_REQUIRED")
            result["generated_source_sha256"] = generated_source_hash(args.source_root)
        require(args.output is not None, "SQLITE_OUTPUT_REQUIRED")
        Path(args.output).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
