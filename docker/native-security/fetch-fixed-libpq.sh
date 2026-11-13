#!/bin/sh
set -eu

output_root="${1:?output directory is required}"
work_root="$(mktemp -d)"
trap 'rm -rf "${work_root}"' EXIT INT TERM
version='18.7-1'
architecture="$(dpkg --print-architecture)"
case "${architecture}" in
    arm64)
        lib_sha='44f3d96487445c984d759c3f22abb746cf7b74da1cc29d805c1a7672a7b46a80'
        dev_sha='896aa43fb50586e9c332c111d4ea7b88e86734a6862840ffb77b4188b3f6ee08'
        ;;
    amd64)
        lib_sha='960f75fa3ff75769bdca702140a58f329f23121466e48033f92ba0db9f3e1231'
        dev_sha='7819e084db7519d15ae050f74c6086758ef0af5c4f1e9d668e07dc02ce633fb5'
        ;;
    *) echo "Unsupported libpq package architecture: ${architecture}" >&2; exit 1 ;;
esac

# Restrict sid to a temporary signed download of these two exact packages.
mkdir -p "${work_root}/lists/partial" "${output_root}"
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian sid main' >"${work_root}/sources.list"
apt_fixed() {
    apt-get -o "Dir::Etc::sourcelist=${work_root}/sources.list" \
        -o 'Dir::Etc::sourceparts=-' -o "Dir::State::lists=${work_root}/lists" "$@"
}
apt_fixed update
(
    cd "${work_root}"
    apt_fixed download "libpq5=${version}" "libpq-dev=${version}"
)
printf '%s  %s\n' \
    "${lib_sha}" "${work_root}/libpq5_${version}_${architecture}.deb" \
    "${dev_sha}" "${work_root}/libpq-dev_${version}_${architecture}.deb" | sha256sum --check

for package in libpq5 libpq-dev; do
    archive="${work_root}/${package}_${version}_${architecture}.deb"
    test "$(dpkg-deb -f "${archive}" Package)" = "${package}"
    test "$(dpkg-deb -f "${archive}" Source)" = 'postgresql-18'
    test "$(dpkg-deb -f "${archive}" Version)" = "${version}"
    test "$(dpkg-deb -f "${archive}" Architecture)" = "${architecture}"
    cp "${archive}" "${output_root}/${package}.deb"
done
