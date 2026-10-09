#!/bin/sh
set -eu

output_root="${1:?output directory is required}"
work_root="$(mktemp -d)"
trap 'rm -rf "${work_root}"' EXIT INT TERM
multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"
mkdir -p "${output_root}"

download() {
    curl --fail --location --silent --show-error "$1" --output "$2"
    printf '%s  %s\n' "$3" "$2" | sha256sum --check
}

# Keep the stable CUPS version and every Debian security patch. OpenSSL is an
# upstream-supported TLS implementation, not a replacement certificate verifier.
download 'https://deb.debian.org/debian/pool/main/c/cups/cups_2.4.10.orig.tar.gz' \
    "${work_root}/source.tar.gz" 'd75757c2bc0f7a28b02ee4d52ca9e4b1aa1ba2affe16b985854f5336940e5ad7'
download 'https://deb.debian.org/debian/pool/main/c/cups/cups_2.4.10-3+deb13u2.debian.tar.xz' \
    "${work_root}/debian.tar.xz" '3f45f3050495890331b91296b495162f2b1f17e805e13ef3509769b732e50ce6'
tar -xzf "${work_root}/source.tar.gz" -C "${work_root}"
source_root="${work_root}/cups-2.4.10"
tar -xJf "${work_root}/debian.tar.xz" -C "${source_root}"
(
    cd "${source_root}"
    QUILT_PATCHES=debian/patches quilt push -a
    autoconf
    CPPFLAGS="$(dpkg-buildflags --get CPPFLAGS)" \
        CFLAGS="$(dpkg-buildflags --get CFLAGS) -fPIC" \
        CXXFLAGS="$(dpkg-buildflags --get CXXFLAGS) -fPIC" \
        DSOFLAGS="$(dpkg-buildflags --get LDFLAGS) -Wl,-z,now" \
        LDFLAGS="$(dpkg-buildflags --get LDFLAGS) -Wl,-z,now" \
        ./configure --prefix=/usr --libdir="/usr/lib/${multiarch}" \
        --with-tls=openssl --enable-threads --enable-static --enable-gssapi \
        --enable-avahi --enable-dbus --enable-libpaper --disable-libusb \
        --disable-launchd --with-cups-group=lp --with-system-groups='root lpadmin' \
        --with-rundir=/run/cups --with-printcap=/run/cups/printcap
    grep '^#define HAVE_OPENSSL 1' config.h
    if grep '^#define HAVE_GNUTLS 1' config.h; then exit 1; fi
    make -C cups -j "$(nproc)" all
    make -C cups -j "$(nproc)" unittests
)

# Preserve the distribution's library identity, ancillary files and package
# lifecycle scripts. Compare every original exported symbol before replacing it.
(
    cd "${work_root}"
    apt-get download 'libcups2t64=2.4.10-3+deb13u2'
)
package_root="${work_root}/package"
dpkg-deb --raw-extract "${work_root}"/libcups2t64_*.deb "${package_root}"
library="${package_root}/usr/lib/${multiarch}/libcups.so.2"
test -f "${library}"
nm -D --defined-only "${library}" | awk '{print $3}' | sort >"${work_root}/old-symbols"
nm -D --defined-only "${source_root}/cups/libcups.so.2" | awk '{print $3}' | sort >"${work_root}/new-symbols"
comm -23 "${work_root}/old-symbols" "${work_root}/new-symbols" >"${work_root}/missing-symbols"
test ! -s "${work_root}/missing-symbols" || { cat "${work_root}/missing-symbols"; exit 1; }
readelf -d "${source_root}/cups/libcups.so.2" >"${work_root}/dynamic"
grep 'Shared library: \[libssl.so.3\]' "${work_root}/dynamic"
grep 'Shared library: \[libcrypto.so.3\]' "${work_root}/dynamic"
if grep -i gnutls "${work_root}/dynamic"; then exit 1; fi
install -m 0644 "${source_root}/cups/libcups.so.2" "${library}"
python - "${package_root}/DEBIAN/control" <<'PY'
from pathlib import Path
import re
import sys

control = Path(sys.argv[1])
text = control.read_text()
assert re.search(r"^Version: 2\.4\.10-3\+deb13u2$", text, re.M), "Unexpected CUPS package version"
text, count = re.subn(r"libgnutls30t64(?: \([^)]*\))?", "libssl3t64 (>= 3.5.0)", text)
assert count == 1 and "gnutls" not in text.lower(), "Unexpected CUPS dependency closure"
text = re.sub(r"^Version: .*", "Version: 2.4.10-3+deb13u2+academy1", text, flags=re.M)
text = re.sub(r"^Maintainer: .*", "Maintainer: Academy Platform <platform@academy.invalid>", text, flags=re.M)
control.write_text(text)
PY
(
    cd "${package_root}"
    find usr -type f -print0 | sort -z | xargs -0 md5sum >DEBIAN/md5sums
)
dpkg-deb --build --root-owner-group "${package_root}" "${output_root}/libcups2t64-openssl.deb"
echo CUPS_OPENSSL_ABI_COMPATIBLE
