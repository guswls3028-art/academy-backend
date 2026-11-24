#!/bin/sh
set -eu

output_root="${1:?output directory is required}"
work_root="$(mktemp -d)"
trap 'rm -rf "${work_root}"' EXIT INT TERM
architecture="$(dpkg --print-architecture)"
upstream_revision='1ad851363d5b8588b0d40edcc045fd633e93f0c2'
upstream_sha256='a15c999e85eab90968cb303eda7ef0589dfc0d341ffafe8b23087d5aa1ad7686'
debian_version='3.5.9-1~deb13u1'
debian_sha256='7d5158a87cd24641316b050de419be4ebddb1f180afd7ebaa3bc990036c38ec6'
version='3.5.10~dev20261124+git1ad85136-1~deb13u1+academy1'
source_root="${work_root}/openssl"

mkdir -p "${source_root}" "${output_root}/runtime" "${output_root}/build"
curl --fail --location --silent --show-error --retry 2 \
    "https://codeload.github.com/openssl/openssl/tar.gz/${upstream_revision}" \
    --output "${work_root}/upstream.tar.gz"
curl --fail --location --silent --show-error --retry 2 \
    "https://deb.debian.org/debian/pool/main/o/openssl/openssl_${debian_version}.debian.tar.xz" \
    --output "${work_root}/debian.tar.xz"
printf '%s  %s\n' "${upstream_sha256}" "${work_root}/upstream.tar.gz" \
    "${debian_sha256}" "${work_root}/debian.tar.xz" | sha256sum --check
tar -xzf "${work_root}/upstream.tar.gz" --strip-components=1 -C "${source_root}"
tar -xJf "${work_root}/debian.tar.xz" -C "${source_root}"

(
    cd "${source_root}"
    grep -qx 'PATCH=10' VERSION.dat
    grep -qx 'PRE_RELEASE_TAG=dev' VERSION.dat
    grep -qx 'SHLIB_VERSION=3' VERSION.dat
    test "$(dpkg-parsechangelog --show-field Version)" = "${debian_version}"

    {
        printf 'openssl (%s) trixie; urgency=high\n\n' "${version}"
        printf '  * Build the pinned OpenSSL 3.5 maintenance snapshot with Debian packaging.\n\n'
        printf ' -- Academy Platform <platform@academy.invalid>  Tue, 24 Nov 2026 08:05:27 +0000\n\n'
        cat debian/changelog
    } >debian/changelog.academy
    mv debian/changelog.academy debian/changelog
    dpkg-buildpackage -B -Pnoudeb -us -uc -jauto
)

for package in openssl libssl3t64 openssl-provider-legacy libssl-dev; do
    artifact="${work_root}/${package}_${version}_${architecture}.deb"
    test "$(dpkg-deb -f "${artifact}" Package)" = "${package}"
    test "$(dpkg-deb -f "${artifact}" Version)" = "${version}"
    test "$(dpkg-deb -f "${artifact}" Architecture)" = "${architecture}"
    destination=runtime
    if [ "${package}" = libssl-dev ]; then destination=build; fi
    install -m 0644 "${artifact}" "${output_root}/${destination}/${package}.deb"
done

printf '%s\n' "${version}" >"${output_root}/runtime/package-version"
printf '{"upstream_revision":"%s","upstream_sha256":"%s","debian_packaging_version":"%s","debian_packaging_sha256":"%s","version":"%s"}\n' \
    "${upstream_revision}" "${upstream_sha256}" "${debian_version}" "${debian_sha256}" "${version}" \
    >"${output_root}/runtime/build.json"
