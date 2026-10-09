#!/bin/sh
set -eu

output_root="${1:?output directory is required}"
work_root="$(mktemp -d)"
trap 'rm -rf "${work_root}"' EXIT INT TERM
multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"
version='1.21.3-5+deb13u1+academy1'
mkdir -p "${output_root}"

download() {
    curl --fail --location --silent --show-error "$1" --output "$2"
    printf '%s  %s\n' "$3" "$2" | sha256sum --check
}

# Retain the installed stable ABI and all Debian security patches. The actual
# upstream version remains 1.21.3: no future release or scanner exception.
download \
    'https://deb.debian.org/debian-security/pool/updates/main/k/krb5/krb5_1.21.3.orig.tar.gz' \
    "${work_root}/source.tar.gz" \
    'b7a4cd5ead67fb08b980b21abd150ff7217e85ea320c9ed0c6dadd304840ad35'
download \
    'https://deb.debian.org/debian-security/pool/updates/main/k/krb5/krb5_1.21.3-5+deb13u1.debian.tar.xz' \
    "${work_root}/debian.tar.xz" \
    '02b873b239fbe7ddf016dfe44deba4130673f4606c18c93da0622e2bc8500fb4'
download \
    'https://github.com/krb5/krb5/commit/48afa9abb89ab2176bb20624d87d010b9984fc08.patch' \
    "${work_root}/array-bound.patch" \
    '955de54b130bc76b07d4d6ca8117eda441cbfc0daa83d93c6d05d776889bc5d0'
download \
    'https://github.com/krb5/krb5/commit/5031b854ad8ba6cce20cdd8c991f81dbc3f924bd.patch' \
    "${work_root}/principal-bound.patch" \
    '12b1fb323f0d02d755d88e4cd3033f49a604baac8724d16f0047a5e83d780cf6'

tar -xzf "${work_root}/source.tar.gz" -C "${work_root}"
source_root="${work_root}/krb5-1.21.3"
tar -xJf "${work_root}/debian.tar.xz" -C "${source_root}"
(
    cd "${source_root}"
    QUILT_PATCHES=debian/patches quilt push -a
    patch --batch --forward -p1 <"${work_root}/array-bound.patch"
    patch --batch --forward -p1 <"${work_root}/principal-bound.patch"
    cd src
    autoreconf --install --force
    CPPFLAGS="$(dpkg-buildflags --get CPPFLAGS)" \
        CFLAGS="$(dpkg-buildflags --get CFLAGS) -fPIC" \
        LDFLAGS="$(dpkg-buildflags --get LDFLAGS) -Wl,-z,now" \
        ./configure --prefix=/usr --libdir="/usr/lib/${multiarch}" \
        --sysconfdir=/etc --localstatedir=/etc --disable-rpath --enable-shared \
        --with-system-et --with-system-ss --with-system-verto --with-lmdb \
        --without-ldap --without-tcl
    make -j "$(nproc)"
    make check
)

# Debian pins GSSAPI to the exact libkrb5 version. Rebuild its four client ABI
# libraries together while preserving plugins, scripts and ancillary files.
apt-get update
(
    cd "${work_root}"
    apt-get download 'libkrb5-3=1.21.3-5+deb13u1' \
        'libgssapi-krb5-2=1.21.3-5+deb13u1' \
        'libk5crypto3=1.21.3-5+deb13u1' 'libkrb5support0=1.21.3-5+deb13u1'
)
ulimit -c 0
for entry in 'libkrb5-3:libkrb5.so.3.3' 'libgssapi-krb5-2:libgssapi_krb5.so.2.2' \
    'libk5crypto3:libk5crypto.so.3.1' 'libkrb5support0:libkrb5support.so.0.1'; do
    package="${entry%%:*}"
    library="${entry#*:}"
    package_root="${work_root}/${package}-package"
    dpkg-deb --raw-extract "${work_root}/${package}"_*.deb "${package_root}"
    original_library="${package_root}/usr/lib/${multiarch}/${library}"
    fixed_library="${source_root}/src/lib/${library}"
    test -f "${original_library}" && test -f "${fixed_library}"
    if [ "${package}" = libkrb5-3 ]; then
        # Require a working valid fixture and real crashes for every bad case.
        python /usr/local/bin/verify-krb5.py --library "${original_library}" --expect-unpatched
        python /usr/local/bin/verify-krb5.py --library "${fixed_library}"
    fi
    nm -D --defined-only "${original_library}" | awk '{print $3}' | sort >"${work_root}/old-symbols"
    nm -D --defined-only "${fixed_library}" | awk '{print $3}' | sort >"${work_root}/new-symbols"
    diff -u "${work_root}/old-symbols" "${work_root}/new-symbols"
    install -m 0644 "${fixed_library}" "${original_library}"
    sed -i "s/^Version: .*/Version: ${version}/; s/^Source: .*/Source: krb5/; s/(= 1.21.3-5+deb13u1)/(= ${version})/g; s/^Maintainer: .*/Maintainer: Academy Platform <platform@academy.invalid>/" "${package_root}/DEBIAN/control"
    grep -Fx 'Source: krb5' "${package_root}/DEBIAN/control"
    (
        cd "${package_root}"
        find usr -type f -print0 | sort -z | xargs -0 md5sum >DEBIAN/md5sums
    )
    dpkg-deb --build --root-owner-group "${package_root}" "${output_root}/${package}-fixed.deb"
done
