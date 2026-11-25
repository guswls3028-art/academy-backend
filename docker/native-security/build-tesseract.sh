#!/bin/sh
set -eu

output_root="${1:?output directory is required}"
work_root="$(mktemp -d)"
trap 'rm -rf "${work_root}"' EXIT INT TERM
version='5.5.4-1+academy1'
base='https://deb.debian.org/debian/pool/main/t/tesseract'
mkdir -p "${output_root}"

download() {
    curl --fail --location --silent --show-error "${base}/$1" --output "${work_root}/$1"
    printf '%s  %s\n' "$2" "${work_root}/$1" | sha256sum --check
}

# Official signed Debian source; rebuild against Trixie, never import forky ICU78.
download tesseract_5.5.4-1.dsc d4b356be8689d004065136e091c823b5e1bd07b84e7d21fee11886bd2fe86e83
download tesseract_5.5.4.orig.tar.gz a8dbf8b8c8ee427ff6ad1d99116502feaf59440af1adb893caf60a6576826cf4
download tesseract_5.5.4.orig-test.tar.gz 2788d27de6e448f8ffa7dd19a22c5781768440dee60a34607099f0c66d44b911
download tesseract_5.5.4-1.debian.tar.xz 4f9a61b32276d686ed27b4bd3a5ac447c40bd97f8a839222db24dfcb656d5189
gpgv --keyring /usr/share/keyrings/debian-keyring.gpg "${work_root}/tesseract_5.5.4-1.dsc"
dpkg-source -x "${work_root}/tesseract_5.5.4-1.dsc" "${work_root}/source"
(
    cd "${work_root}/source"
    test "$(cat VERSION)" = '5.5.4'
    sed -i "1s/(5.5.4-1)/(5.5.4-1+academy1)/" debian/changelog
    # Retain Debian build flags, dependency generation, package/source names and tests.
    DEB_BUILD_OPTIONS=parallel=2 dpkg-buildpackage --build=binary --unsigned-source --unsigned-changes
)
for package in libtesseract5 tesseract-ocr; do
    set -- "${work_root}/${package}_${version}_"*.deb
    test "$#" -eq 1 && test -f "$1"
    test "$(dpkg-deb -f "$1" Package)" = "${package}"
    test "$(dpkg-deb -f "$1" Version)" = "${version}"
    test "$(dpkg-deb -f "$1" Source)" = 'tesseract'
    cp "$1" "${output_root}/${package}.deb"
done
