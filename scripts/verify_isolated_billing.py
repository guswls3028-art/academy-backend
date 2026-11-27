"""One-shot billing acceptance check. Use an isolated Python process and supervised pipes."""

import json
import os
import re
import stat
import sys
import threading
from pathlib import Path


TEST_SETTINGS = "apps.api.config.settings.test"
MEMORY_DATABASES = {":memory:", "file:memorydb_default?mode=memory&cache=shared"}


def protect_child():
    if not sys.flags.isolated or not sys.dont_write_bytecode or sys.flags.optimize:
        raise RuntimeError("Isolated non-optimized Python with bytecode writes off is required")
    safe_env = {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    if sys.platform == "win32":
        # libuv populates these identity/profile keys even with an explicit subprocess env.
        # Accept only deliberately inert values, never the enclosing user's profile.
        for name in ("HOMEDRIVE", "HOMEPATH", "LOGONSERVER", "SYSTEMDRIVE", "TEMP",
                     "USERDOMAIN", "USERNAME", "USERPROFILE"):
            if name in os.environ and os.environ[name] == "NUL":
                safe_env.add(name)
    if any(key.upper() not in safe_env for key in os.environ):
        raise RuntimeError("Refusing an inherited application environment")
    if sys.platform.startswith("linux"):
        # The outside executor must also bind the actual image digest and check Docker mounts.
        status = Path("/proc/self/status").read_text().splitlines()
        capabilities = next(line.split()[1] for line in status if line.startswith("CapEff:"))
        if (os.geteuid() != 1000 or int(capabilities, 16) or not os.statvfs("/").f_flag & os.ST_RDONLY
                or {p.name for p in Path("/sys/class/net").iterdir()} != {"lo"}):
            raise RuntimeError("The Linux image needs an unprivileged read-only, network-isolated process")
        # The API Dockerfile installs dependencies with --user. Isolated Python excludes
        # that directory, so load only the fixed candidate-image package path.
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        packages = Path("/home/appuser/.local/lib") / version / "site-packages"
        if packages.resolve(strict=True) != packages or not os.statvfs(packages).f_flag & os.ST_RDONLY:
            raise RuntimeError("The packaged API dependencies must be read-only")
        sys.path.append(str(packages))
    elif sys.platform != "win32":
        raise RuntimeError("Unsupported probe host")

    def audit(event, args):
        if event == "open" and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            if args[0] != os.devnull:
                raise PermissionError("The billing probe cannot write filesystem data")
        if event in {"os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.link", "os.symlink", "os.truncate"}:
            raise PermissionError("The billing probe cannot mutate filesystem data")
        if event == "sqlite3.connect" and args[0] not in MEMORY_DATABASES:
            raise PermissionError("The billing probe only opens its own in-memory database")
        if event in {"socket.bind", "socket.connect", "socket.sendto", "socket.getaddrinfo",
                     "subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "os.exec"}:
            raise PermissionError("The billing probe cannot open a listener, contact a service or launch a process")

    sys.addaudithook(audit)
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    os.environ["AWS_CONFIG_FILE"] = os.devnull
    os.environ["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
    os.environ["DJANGO_SETTINGS_MODULE"] = TEST_SETTINGS


def main():
    protect_child()
    if not all(stat.S_ISFIFO(os.fstat(stream.fileno()).st_mode) for stream in (sys.stdin, sys.stdout)):
        raise RuntimeError("A supervised pipe is required for the billing probe")
    # Apply the deadline even if the supervisor opens the pipe but never sends a record.
    deadline = threading.Timer(900, lambda: os._exit(22))
    deadline.daemon = True
    deadline.start()
    record = sys.stdin.buffer.readline(2048)
    if not record.endswith(b"\n"):
        raise ValueError("The probe requires one bounded, complete binding record")
    binding = json.loads(record)
    fields = {"nonce": 32, "pinned_backend_revision": 40, "frontend_version": 40,
              "archive_sha256": 64, "dist_fingerprint": 64, "runtime_assets_sha256": 64}
    if not isinstance(binding, dict) or set(binding) != set(fields) | {"image"} or any(
        not isinstance(binding[k], str) or not re.fullmatch(r"[0-9a-f]{%d}" % size, binding[k])
        for k, size in fields.items()
    ):
        raise ValueError("Invalid offline artifact binding")
    image = binding["image"]
    if sys.platform.startswith("linux") and image is None:
        raise ValueError("The Linux image check requires its candidate identity")
    if image is not None:
        expected = {"digest": r"sha256:[0-9a-f]{64}", "source": r"[0-9a-f]{40}",
                    "release": r"sha-[0-9a-f]{40}-run-[0-9]+-[0-9]+", "instance": r"i-[0-9a-f]{8,17}",
                    "container": r"[0-9a-f]{64}", "lease_hash": r"[0-9a-f]{64}"}
        if (not sys.platform.startswith("linux") or not isinstance(image, dict) or set(image) != set(expected)
                or any(not isinstance(image[k], str) or not re.fullmatch(pattern, image[k])
                       for k, pattern in expected.items())
                or not image["release"].startswith("sha-" + image["source"] + "-run-")):
            raise ValueError("Invalid candidate image correlation")

    # Avoid a blocking read on Windows stdin. Django also checks that descriptor during
    # management commands. A heartbeat to the supervisor's private pipe detects its exit.
    finished = threading.Event()

    def watch_supervisor():
        while not finished.wait(1):
            try:
                os.write(sys.stdout.fileno(), b"BILLING_PROBE_ALIVE\n")
            except OSError:
                os._exit(21)

    threading.Thread(target=watch_supervisor, daemon=True).start()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import django
    from django.conf import settings

    databases = settings.DATABASES
    if (settings.SETTINGS_MODULE != TEST_SETTINGS or set(databases) != {"default"}
            or databases["default"]["ENGINE"] != "django.db.backends.sqlite3"
            or databases["default"]["NAME"] != ":memory:"):
        raise RuntimeError("The selected configuration is not the private SQLite test database")
    django.setup()
    from django.test.runner import DiscoverRunner

    class OneSettlementCheck(DiscoverRunner):
        def run_suite(self, suite, **kwargs):
            result = super().run_suite(suite, **kwargs)
            if result.testsRun != 1 or result.skipped or not result.wasSuccessful():
                raise RuntimeError("A single executed settlement check must pass")
            return result

    try:
        outcome = OneSettlementCheck(interactive=False, verbosity=0).run_tests([
            "apps.billing.qa_image_settlement.ImageSettlementProbe.test_settlement",
        ])
        if outcome:
            raise RuntimeError("The isolated test database did not finish its checks")
    finally:
        finished.set()
        deadline.cancel()
    print("BILLING_PROBE_RESULT " + json.dumps({
        "scope": "disposable-process", "host": sys.platform, "binding": binding, "payments": [1, 1],
        "provider_calls": 0, "remaining_billing_rows": 0,
    }), flush=True)


if __name__ == "__main__":
    main()
