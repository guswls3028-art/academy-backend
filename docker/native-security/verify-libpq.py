"""Check that Psycopg loads the patched, package-owned libpq."""

import subprocess
from importlib.metadata import version
from pathlib import Path

import psycopg2
from psycopg2 import extensions


package = subprocess.check_output(
    ["dpkg-query", "-W", "-f=${source:Package}\t${Version}", "libpq5"], text=True,
)
assert package == "postgresql-18\t18.7-1", package
assert version("psycopg2-binary") == "2.9.12"
assert psycopg2.__libpq_version__ == extensions.libpq_version() == 180007

owned_libraries = {
    Path(path).resolve()
    for path in subprocess.check_output(["dpkg-query", "-L", "libpq5"], text=True).splitlines()
    if Path(path).name.startswith("libpq.so.5")
}
loaded_libraries = set()
for mapping in Path("/proc/self/maps").read_text().splitlines():
    fields = mapping.split(maxsplit=5)
    if len(fields) == 6 and Path(fields[5]).name.startswith(("libpq.so", "libpq-")):
        loaded_libraries.add(Path(fields[5]).resolve())
assert len(loaded_libraries) == 1 and loaded_libraries <= owned_libraries, loaded_libraries
print("Psycopg links to the packaged libpq 18.7")
