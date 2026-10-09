"""Exercise the real system krb5_rd_cred ABI with local, unencrypted fixtures.

No KDC, user credential or network is used. Each case runs in a child process
so the unpatched NULL dereference is observable without killing the build driver.
This does not attest to separately bundled libraries in Python wheels.
"""

import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys


VERSION = "1.21.3-5+deb13u1+academy1"
MODIFIED = -1765328343
CASES = ("valid", "extra-ticket", "missing-client", "missing-server")


def der(tag, body):
    size = len(body)
    length = (
        bytes([size])
        if size < 128
        else bytes([0x80 | ((size.bit_length() + 7) // 8)]) + size.to_bytes((size.bit_length() + 7) // 8, "big")
    )
    return bytes([tag]) + length + body


def sequence(*parts):
    return der(0x30, b"".join(parts))


def field(number, body):
    return der(0xA0 + number, body)


def integer(value):
    return der(2, bytes([value]))


def principal(name):
    return sequence(field(0, integer(1)), field(1, sequence(der(0x1B, name))))


def fixture(case):
    realm = der(0x1B, b"ACADEMY.INVALID")
    server = principal(b"test-service")
    encrypted = lambda data: sequence(field(0, integer(0)), field(2, der(4, data)))
    ticket = der(0x61, sequence(field(0, integer(5)), field(1, realm), field(2, server), field(3, encrypted(b""))))
    info = [field(0, sequence(field(0, integer(17)), field(1, der(4, b"0" * 16))))]
    if case != "missing-client":
        info += [field(1, realm), field(2, principal(b"qa-native-probe"))]
    if case != "missing-server":
        info += [field(8, realm), field(9, server)]
    encpart = der(0x7D, sequence(field(0, sequence(sequence(*info)))))
    return der(
        0x76,
        sequence(
            field(0, integer(5)),
            field(1, integer(22)),
            field(2, sequence(ticket, *([ticket] if case == "extra-ticket" else []))),
            field(3, encrypted(encpart)),
        ),
    )


def probe(case, library):
    class Data(ctypes.Structure):
        _fields_ = [("magic", ctypes.c_int32), ("length", ctypes.c_uint), ("data", ctypes.c_void_p)]

    lib = ctypes.CDLL(library)
    pointer = ctypes.c_void_p
    signatures = {
        "krb5_init_context": ([ctypes.POINTER(pointer)], ctypes.c_int32),
        "krb5_auth_con_init": ([pointer, ctypes.POINTER(pointer)], ctypes.c_int32),
        "krb5_auth_con_setflags": ([pointer, pointer, ctypes.c_int32], ctypes.c_int32),
        "krb5_rd_cred": ([pointer, pointer, ctypes.POINTER(Data), ctypes.POINTER(pointer), pointer], ctypes.c_int32),
        "krb5_free_tgt_creds": ([pointer, pointer], None),
        "krb5_auth_con_free": ([pointer, pointer], ctypes.c_int32),
        "krb5_free_context": ([pointer], None),
    }
    for name, (args, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result
    context, auth, credentials = pointer(), pointer(), pointer()
    assert lib.krb5_init_context(ctypes.byref(context)) == 0, "KRB5_CONTEXT_FAILED"
    try:
        assert lib.krb5_auth_con_init(context, ctypes.byref(auth)) == 0, "KRB5_AUTH_CONTEXT_FAILED"
        # The synthetic fixture deliberately has no timestamp/replay cache.
        assert lib.krb5_auth_con_setflags(context, auth, 0) == 0, "KRB5_FLAGS_FAILED"
        payload = fixture(case)
        buffer = ctypes.create_string_buffer(payload)
        data = Data(0, len(payload), ctypes.cast(buffer, pointer))
        result = lib.krb5_rd_cred(context, auth, ctypes.byref(data), ctypes.byref(credentials), None)
        expected = 0 if case == "valid" else MODIFIED
        assert result == expected, f"KRB5_{case}_RESULT_{result}_EXPECTED_{expected}"
        if case == "valid":
            assert credentials.value, "KRB5_VALID_NO_CREDENTIAL"
            values = ctypes.cast(credentials, ctypes.POINTER(pointer))
            assert values[0] and not values[1], "KRB5_VALID_CREDENTIAL_COUNT"
    finally:
        if credentials.value:
            lib.krb5_free_tgt_creds(context, credentials)
        if auth.value:
            lib.krb5_auth_con_free(context, auth)
        lib.krb5_free_context(context)


def verify(library="libkrb5.so.3", *, expect_unpatched=False, package=False):
    if package:
        for key, expected in (("Version", VERSION), ("Source", "krb5")):
            actual = subprocess.check_output(["dpkg-query", "-W", f"-f=${{{key}}}", "libkrb5-3"], text=True)
            assert actual == expected, f"KRB5_PACKAGE_{key}_MISMATCH"
    for case in CASES:
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--case", case, "--library", library],
            env={**os.environ, "KRB5_CONFIG": "/dev/null", "KRB5_KTNAME": "FILE:/nonexistent-academy-qa-keytab"},
            capture_output=True,
            text=True,
            timeout=20,
        )
        expected = -11 if expect_unpatched and case != "valid" else 0
        if child.returncode != expected:
            raise RuntimeError(f"KRB5_{case}_PROCESS_{child.returncode}_EXPECTED_{expected}: {child.stderr[-600:]}")
    print("KRB5_UNPATCHED_NULL_REPRODUCED" if expect_unpatched else "KRB5_VALID_AND_MALFORMED_PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--library", default="libkrb5.so.3")
    parser.add_argument("--expect-unpatched", action="store_true")
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    if args.case:
        probe(args.case, args.library)
    else:
        verify(args.library, expect_unpatched=args.expect_unpatched, package=args.package)
