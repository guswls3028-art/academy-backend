#!/bin/sh
set -eu

output_root="${1:?output directory is required}"
work_root="$(mktemp -d)"
trap 'rm -rf "${work_root}"' EXIT INT TERM
multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"
architecture="$(dpkg --print-architecture)"
version='3.54.1-1+academy1'
source_url='https://sqlite.org/2026/sqlite-src-3540100.zip'
source_sha256='387e7f6cad4797b17848c07d453dc6606917bb356a6cfe5a3abafd38c240c71b'
mkdir -p "${output_root}"

# Keep the stable distribution, libc and public SQLite ABI. Capture the actual
# installed library before replacing it; only normal SQL is exercised below.
test "$(dpkg-query -W -f='${Source}' libsqlite3-0)" = sqlite3
test "$(dpkg-query -W -f='${Version}' libsqlite3-0)" = '3.46.1-7+deb13u2'
test "$(dpkg-query -W -f='${Version}' libsqlite3-dev)" = '3.46.1-7+deb13u2'
# This old source is used only to classify the baseline's private declarations.
# No old binary/source is installed in the new runtime package.
curl --fail --location --silent --show-error \
    'https://sqlite.org/2024/sqlite-autoconf-3460100.tar.gz' --output "${work_root}/baseline.tar.gz"
printf '%s  %s\n' '67d3fe6d268e6eaddcae3727fce58fcc8e9c53869bdd07a0c61e38ddf2965071' \
    "${work_root}/baseline.tar.gz" | sha256sum --check
tar -xzf "${work_root}/baseline.tar.gz" -C "${work_root}"
python /usr/local/bin/verify-sqlite.py --capture-baseline \
    "/usr/lib/${multiarch}/libsqlite3.so.0" --public-header /usr/include/sqlite3.h \
    --private-source "${work_root}/sqlite-autoconf-3460100/sqlite3.c" \
    --output "${work_root}/baseline.json"
curl --fail --location --silent --show-error "${source_url}" --output "${work_root}/source.zip"
printf '%s  %s\n' "${source_sha256}" "${work_root}/source.zip" | sha256sum --check
python -m zipfile -e "${work_root}/source.zip" "${work_root}"
source_root="${work_root}/sqlite-src-3540100"
python /usr/local/bin/verify-sqlite.py --source "${source_root}" --archive "${work_root}/source.zip"

# SQLite supports building the amalgamation as a shared library directly.
# Preserve every reported stable compile option (except the compiler identity),
# with validated macro tokens rather than eval or an unreviewed --all preset.
python /usr/local/bin/verify-sqlite.py --compiler-flags "${work_root}/baseline.json" >"${work_root}/flags"
set -f
sqlite_flags="$(cat "${work_root}/flags")"
# Generate the parser with the same options used to compile the library.
# The pre-generated amalgamation cannot implement UPDATE/DELETE LIMIT merely
# by adding a compiler macro. Build no CLI, tests or executable SQL fixtures.
make -C "${source_root}" -f Makefile.linux-generic sqlite3.c \
    B.tclsh=tclsh "OPTS=${sqlite_flags}"
library="${work_root}/libsqlite3.so.0.3.54.1"
# Intentional splitting: compiler flags are validated as single macro tokens.
cc $(dpkg-buildflags --get CPPFLAGS) $(dpkg-buildflags --get CFLAGS) \
    -fPIC -D_GNU_SOURCE $sqlite_flags -shared -pthread \
    "${source_root}/sqlite3.c" -o "${library}" \
    $(dpkg-buildflags --get LDFLAGS) -Wl,-z,now -Wl,-soname,libsqlite3.so.0 -ldl -lm
python /usr/local/bin/verify-sqlite.py --verify-build "${library}" \
    --baseline "${work_root}/baseline.json" --source-root "${source_root}" \
    --output "${work_root}/build.json"

package_root="${work_root}/package"
install -D -m 0644 "${library}" "${package_root}/usr/lib/${multiarch}/$(basename "${library}")"
ln -s "$(basename "${library}")" "${package_root}/usr/lib/${multiarch}/libsqlite3.so.0"
install -D -m 0644 "${work_root}/baseline.json" "${package_root}/usr/share/academy/native-security/sqlite-baseline.json"
install -D -m 0644 "${work_root}/build.json" "${package_root}/usr/share/academy/native-security/sqlite-build.json"
install -D -m 0644 "${source_root}/sqlite3.h" "${package_root}/usr/share/doc/libsqlite3-0/upstream-header.txt"
mkdir -p "${package_root}/DEBIAN"
cat >"${package_root}/DEBIAN/control" <<EOF
Package: libsqlite3-0
Source: sqlite3
Version: ${version}
Architecture: ${architecture}
Maintainer: Academy Platform <platform@academy.invalid>
Depends: libc6 (>= 2.41)
Section: libs
Priority: optional
Multi-Arch: same
Description: SQLite shared library from the official 3.54.1 release
 Checksum-pinned upstream source with the existing stable SQLite ABI and options.
 No command-line tools are included.
EOF
printf 'activate-noawait ldconfig\n' >"${package_root}/DEBIAN/triggers"
(
    cd "${package_root}"
    find usr -type f -print0 | sort -z | xargs -0 md5sum >DEBIAN/md5sums
)
dpkg-deb --build --root-owner-group "${package_root}" "${output_root}/libsqlite3-0-fixed.deb"
