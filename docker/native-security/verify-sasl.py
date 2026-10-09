"""Verify the system Cyrus SASL runtime has no DIGEST-MD5 client mechanism.

CVE-2026-107161 is in the separately packaged DIGEST-MD5 plugin. This
check preserves the SASL ABI needed by LDAP/libpq/curl and does not claim
that upstream Cyrus SASL or unrelated wheel-vendored libraries are fixed.
"""
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import subprocess


def check_packages(rows):
    cyrus = [row for row in rows if row[1] == "cyrus-sasl2"]
    allowed = {"libsasl2-2", "libsasl2-modules-db"}
    unexpected = [row[0] for row in cyrus if row[0] not in allowed]
    if unexpected:
        raise RuntimeError(f"Unreviewed Cyrus SASL plugin packages: {unexpected}")
    return cyrus


def check_mechanisms(mechanisms, digest_start_result):
    if "DIGEST-MD5" in {name.upper() for name in mechanisms}:
        raise RuntimeError("DIGEST-MD5 is available in the system SASL mechanism list")
    # SASL_NOMECH, from the public Cyrus SASL ABI. Initialization errors or
    # missing callbacks are not evidence that the requested mechanism is absent.
    if digest_start_result != -4:
        raise RuntimeError(f"DIGEST-MD5 was not rejected with SASL_NOMECH: {digest_start_result}")


def main():
    if os.environ.get("SASL_PATH"):
        raise RuntimeError("Custom SASL_PATH requires a separate runtime review")
    output = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Package}\t${source:Package}\t${Version}\n"], text=True,
    )
    packages = check_packages([line.split("\t") for line in output.splitlines() if line.count("\t") == 2])
    for root in (Path("/usr/lib"), Path("/usr/local/lib")):
        if any(root.rglob("libdigestmd5*")):
            raise RuntimeError(f"DIGEST-MD5 plugin file exists under {root}")

    library = ctypes.util.find_library("sasl2")
    if not library:
        if packages:
            raise RuntimeError("Installed Cyrus SASL packages have no loadable core library")
        print("ACADEMY_SASL_DIGEST_ABSENT system-library=absent")
        return
    lib = ctypes.CDLL(library)
    lib.sasl_client_init.argtypes = [ctypes.c_void_p]
    lib.sasl_client_init.restype = ctypes.c_int
    lib.sasl_global_listmech.argtypes = []
    lib.sasl_global_listmech.restype = ctypes.POINTER(ctypes.c_char_p)
    lib.sasl_client_new.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
    lib.sasl_client_new.restype = ctypes.c_int
    lib.sasl_client_start.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_uint),
                                     ctypes.POINTER(ctypes.c_char_p)]
    lib.sasl_client_start.restype = ctypes.c_int
    lib.sasl_dispose.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    lib.sasl_dispose.restype = None
    lib.sasl_done.argtypes = []
    lib.sasl_done.restype = None
    if lib.sasl_client_init(None) != 0:
        raise RuntimeError("System SASL client initialization failed")
    conn = ctypes.c_void_p()
    try:
        names = lib.sasl_global_listmech()
        mechanisms = []
        if not names:
            raise RuntimeError("System SASL mechanism enumeration failed")
        for index in range(128):
            if not names[index]:
                break
            mechanisms.append(names[index].decode("ascii"))
        else:
            raise RuntimeError("Unexpected SASL mechanism count")
        if "EXTERNAL" not in mechanisms:
            raise RuntimeError("SASL core EXTERNAL mechanism is missing")
        if lib.sasl_client_new(b"academy-check", b"localhost", None, None, None, 0, ctypes.byref(conn)) != 0:
            raise RuntimeError("System SASL client creation failed")
        interaction, response, length, selected = ctypes.c_void_p(), ctypes.c_char_p(), ctypes.c_uint(), ctypes.c_char_p()
        code = lib.sasl_client_start(conn, b"DIGEST-MD5", ctypes.byref(interaction), ctypes.byref(response),
                                     ctypes.byref(length), ctypes.byref(selected))
        check_mechanisms(mechanisms, code)
        print("ACADEMY_SASL_DIGEST_ABSENT " + json.dumps({"packages": packages, "mechanisms": mechanisms,
                                                      "digestStartResult": code}))
    finally:
        if conn.value:
            lib.sasl_dispose(ctypes.byref(conn))
        lib.sasl_done()


if __name__ == "__main__":
    main()
