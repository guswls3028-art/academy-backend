"""Rebuild only the matching CPython XML extension pair against fixed Expat."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig


source, expat_source, expat_build, output = map(Path, sys.argv[1:])
assert sys.implementation.name == "cpython"
assert sys.version_info[:3] == (3, 11, 15)
assert sysconfig.get_config_var("SOABI") == "cpython-311-aarch64-linux-gnu"
assert sysconfig.get_config_var("DESTSHARED") == "/usr/local/lib/python3.11/lib-dynload"
assert '#define PY_VERSION              "3.11.15"' in (source / "Include/patchlevel.h").read_text()
output.mkdir()
suffix = sysconfig.get_config_var("EXT_SUFFIX")
includes = [
    expat_build, expat_source / "lib", Path(sysconfig.get_path("include")),
    source / "Include", source / "Include/internal",
]
for module in ["pyexpat", "_elementtree"]:
    assert (Path(sysconfig.get_config_var("DESTSHARED")) / f"{module}{suffix}").is_file()
    command = [
        *shlex.split(sysconfig.get_config_var("LDSHARED")),
        *shlex.split(sysconfig.get_config_var("CFLAGS")),
        "-fPIC", "-DPy_BUILD_CORE_MODULE=1", "-Wl,-z,relro", "-Wl,-z,now",
        *(f"-I{directory}" for directory in includes),
        str(source / "Modules" / f"{module}.c"),
        "-o", str(output / f"{module}{suffix}"),
    ]
    if module == "pyexpat":
        command.extend([f"-L{expat_build / 'lib/.libs'}", "-lexpat"])
    subprocess.run(command, check=True)

dynamic = subprocess.check_output(["readelf", "-d", str(output / f"pyexpat{suffix}")], text=True)
assert "Shared library: [libexpat.so.1]" in dynamic
assert "RPATH" not in dynamic and "RUNPATH" not in dynamic
symbols = subprocess.check_output(["nm", "-D", "--defined-only", str(output / f"pyexpat{suffix}")], text=True)
assert "XML_Parse" not in symbols and "PyExpat_XML_Parse" not in symbols
env = os.environ.copy()
env["PYTHONPATH"] = os.pathsep.join([str(output), str(source / "Lib")])
env["LD_LIBRARY_PATH"] = str(expat_build / "lib/.libs")
subprocess.run([
    sys.executable, "/usr/local/bin/verify-expat.py",
    "--library", str(expat_build / "lib/.libs/libexpat.so.1"), "--python", "--module-dir", str(output),
], env=env, check=True)
subprocess.run([
    sys.executable, "-m", "unittest", "test.test_pyexpat", "test.test_xml_etree",
    "test.test_xml_etree_c", "test.test_minidom", "test.test_sax",
], env=env, check=True)
(output.parent / "python-xml-build.json").write_text(json.dumps({
    "pythonVersion": "3.11.15",
    "pythonSourceSha256": "272179ddd9a2e41a0fc8e42e33dfbdca0b3711aa5abf372d3f2d51543d09b625",
    "soabi": sysconfig.get_config_var("SOABI"),
    "modules": ["pyexpat", "_elementtree"],
    "expatVersion": "2.8.4",
    "expatPackageVersion": "2.8.4+academy1-1",
    "upstreamFix": "0cfd15bdf4b2c22d6b0df73610709dfb60921091",
    "upstreamRegression": "28fcfba540f6933aa8904a1514c4811713d2ab72",
    "systemExpatLinked": True,
}, indent=2) + "\n")
