"""Install the official, architecture-specific rhwp 0.8.7 release in Tools only."""
import hashlib
import io
from pathlib import Path
import platform
import tarfile
from urllib.request import urlopen

RELEASE = "v0.8.7"
ARCHIVES = {
    "x86_64": ("linux-x86_64", "24de2bdaa0b69f86302a27e7fdfb570ad3c53b0ab3a4eac06446adc651dfb988"),
    "aarch64": ("linux-aarch64", "8c9d75ce14cdc3c34d3c7d843a44de3e77ab0dff04a5b189f23fea21999b4455"),
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
