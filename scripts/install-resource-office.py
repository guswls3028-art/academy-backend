"""Install the pinned official headless Office conversion components in Tools.

The vendor packages avoid Debian libreoffice-common's ucf/Perl dependency.
Do not install the desktop, Java/Python/JavaScript macro providers or updater.
"""
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

VERSION = "26.8.1"
SERIES = "26.8"
ARCHIVES = {
    "x86_64": ("x86_64", "x86-64", "30903df3b9f61360d9660cd707de48cd2831469114492a5008ed58a0ac77d044"),
    "aarch64": ("aarch64", "aarch64", "1b069c20dd237f6decad3ea02cb02f39fdf458b03d09c2a9facb7b9b71e6a27a"),
}
PACKAGES = {
    f"libreoffice{SERIES}-ure", f"libreoffice{SERIES}",
    *[f"libobasis{SERIES}-{part}" for part in (
        "core", "ooofonts", "images", "writer", "calc", "impress", "draw", "math", "graphicfilter", "en-us",
    )],
}


def install(archive_path=None):
    directory, architecture, expected = ARCHIVES[platform.machine()]
    with tempfile.TemporaryDirectory(prefix="resource-office-install-") as temp:
        root = Path(temp)
        archive = Path(archive_path) if archive_path else root / "office.tar.gz"
        if not archive_path:
            url = (f"https://download.documentfoundation.org/libreoffice/stable/{VERSION}/deb/{directory}/"
                   f"LibreOffice_{VERSION}_Linux_{architecture}_deb.tar.gz")
            with urlopen(url, timeout=120) as response, archive.open("wb") as target:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 300 * 1024 * 1024:
                        raise ValueError("Office archive size limit")
                    target.write(chunk)
        with archive.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("Official Office renderer checksum mismatch")
        selected = {}
        with tarfile.open(archive, "r:gz") as package:
            for member in package:
                name = Path(member.name).name
                identity = name.split("_", 1)[0]
                if not name.endswith(".deb") or identity not in PACKAGES:
                    continue
                if not member.isfile() or member.size > 240 * 1024 * 1024 or identity in selected:
                    raise ValueError("Invalid Office package member")
                destination = root / name
                with package.extractfile(member) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                selected[identity] = str(destination)
        if set(selected) != PACKAGES:
            raise ValueError("Incomplete Office renderer distribution")
        subprocess.run(["dpkg", "-i", *selected.values()], check=True)
        subprocess.run(["apt-get", "check"], check=True)
    binary = Path(f"/opt/libreoffice{SERIES}/program/soffice")
    subprocess.run([str(binary), "--headless", "--version"], check=True)
    Path("/usr/local/bin/resource-office").symlink_to(binary)


if __name__ == "__main__":
    install(sys.argv[1] if len(sys.argv) == 2 else None)
