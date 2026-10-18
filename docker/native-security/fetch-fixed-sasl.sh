#!/bin/sh
set -eu

output_root="${1:?output directory is required}"
work_root="$(mktemp -d)"
trap 'rm -rf "${work_root}"' EXIT INT TERM
version='2.1.28+dfsg1-12'
architecture="$(dpkg --print-architecture)"
case "${architecture}" in
    arm64)
        core_sha='5ed715f5edba72341b8ba60176049501b6aed6dc27e90668fd53231d733e3abe'
        db_sha='57afe2c4d519ef3a656dce6288f1fe7cf7d77af571d084b93e31a80c2ef22c6d'
        ;;
    amd64)
        core_sha='ae3aabeb7b739da38ff8c7f5c7c60b8d77ac25a485aa65cda211958c3329d1e9'
        db_sha='83d9b0c718153cc323c0d3e6dc52b9c2a94660dd2a50761a766010c6e9fefa11'
        ;;
    *) echo "Unsupported Cyrus package architecture: ${architecture}" >&2; exit 1 ;;
esac

# Authenticate Debian's fixed packages in a temporary, download-only APT state.
# Never add unstable to the builder/runtime sources or upgrade its dependencies.
mkdir -p "${work_root}/lists/partial" "${output_root}"
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian sid main' >"${work_root}/sources.list"
apt_fixed() {
    apt-get -o "Dir::Etc::sourcelist=${work_root}/sources.list" \
        -o 'Dir::Etc::sourceparts=-' -o "Dir::State::lists=${work_root}/lists" "$@"
}
apt_fixed update
(
    cd "${work_root}"
    apt_fixed download "libsasl2-2=${version}" "libsasl2-modules-db=${version}"
)
printf '%s  %s\n' \
    "${core_sha}" "${work_root}/libsasl2-2_${version}_${architecture}.deb" \
    "${db_sha}" "${work_root}/libsasl2-modules-db_${version}_${architecture}.deb" | sha256sum --check

# Preserve every previously exported core ABI symbol used by LDAP/libpq/curl.
multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"
dpkg-deb -x "${work_root}/libsasl2-2_${version}_${architecture}.deb" "${work_root}/fixed"
nm -D --defined-only "/usr/lib/${multiarch}/libsasl2.so.2" | awk '{print $3}' | LC_ALL=C sort -u >"${work_root}/old-symbols"
nm -D --defined-only "${work_root}/fixed/usr/lib/${multiarch}/libsasl2.so.2" | awk '{print $3}' | LC_ALL=C sort -u >"${work_root}/new-symbols"
test -s "${work_root}/old-symbols" && test -s "${work_root}/new-symbols"
test -z "$(LC_ALL=C comm -23 "${work_root}/old-symbols" "${work_root}/new-symbols")"
for package in libsasl2-2 libsasl2-modules-db; do
    archive="${work_root}/${package}_${version}_${architecture}.deb"
    test "$(dpkg-deb -f "${archive}" Package)" = "${package}"
    test "$(dpkg-deb -f "${archive}" Source)" = 'cyrus-sasl2'
    test "$(dpkg-deb -f "${archive}" Version)" = "${version}"
    test "$(dpkg-deb -f "${archive}" Architecture)" = "${architecture}"
    cp "${archive}" "${output_root}/${package}-fixed.deb"
done
