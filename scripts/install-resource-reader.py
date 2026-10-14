"""Install the official, architecture-specific rhwp 0.8.7 release in Tools only."""
import hashlib
import io
from pathlib import Path
import platform
import tarfile
from urllib.request import urlopen

RELEASE = "v0.8.7"
# Official 2026-10-11 Native Skia repack, verified against release SHA256SUMS.txt.
ARCHIVES = {
    "x86_64": ("linux-x86_64", "f1e541a25cfe76b6bc1daf18bf9c1f9a5ac5e03e2c01b188daaf54107214f631"),
    "aarch64": ("linux-aarch64", "c4a70f5ed11b99586ee89d1ac9069145d58a0831d00e87858bb01626e2ebf313"),
}


def install():
    architecture, expected = ARCHIVES[platform.machine()]
    url = f"https://github.com/edwardkim/rhwp/releases/download/{RELEASE}/rhwp-{RELEASE}-{architecture}.tar.gz"
    with urlopen(url, timeout=60) as response:
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Native resource renderer checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member_name, destination in (
            ("rhwp/rhwp", Path("/usr/local/bin/rhwp")),
            ("rhwp/LICENSE", Path("/usr/local/share/licenses/rhwp/LICENSE")),
        ):
            member = archive.getmember(member_name)
            if not member.isfile() or member.size > 80 * 1024 * 1024:
                raise ValueError("Unexpected native resource renderer archive")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source:
                destination.write_bytes(source.read())
            destination.chmod(0o755 if destination.name == "rhwp" else 0o644)


if __name__ == "__main__":
    install()
