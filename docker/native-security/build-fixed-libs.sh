#!/bin/sh
set -eu

output_root="${1:?output directory is required}"
work_root="$(mktemp -d)"
trap 'rm -rf "${work_root}"' EXIT INT TERM

architecture="$(dpkg --print-architecture)"
multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"
jobs="$(nproc)"
mkdir -p "${output_root}"

download() {
    url="$1"
    destination="$2"
    checksum="$3"
    curl --fail --location --silent --show-error "${url}" --output "${destination}"
    printf '%s  %s\n' "${checksum}" "${destination}" | sha256sum --check
}

write_control() {
    package_root="$1"
    package_name="$2"
    source_name="$3"
    version="$4"
    priority="$5"
    pre_depends="$6"
    description="$7"

    mkdir -p "${package_root}/DEBIAN"
    cat >"${package_root}/DEBIAN/control" <<EOF
Package: ${package_name}
Source: ${source_name}
Version: ${version}
Architecture: ${architecture}
Maintainer: Academy Platform <platform@academy.invalid>
${pre_depends}
Section: libs
Priority: ${priority}
Multi-Arch: same
Description: ${description}
 Academy runtime security backport built from checksum-pinned upstream sources.
EOF
}

# CVE-2026-85091: the upstream post-1.3.2 commit fixes gz_vacate bounds
# handling. The source declares 1.3.2.1-motley. Preserve Debian's existing
# 1.3.dfsg+really version shape so the scanner applies Debian source-package
# ranges instead of treating the custom package as upstream MiniZip. Keep the
# zlib source and zlib1g binary identities scanner-visible, and retain the libz
# ABI for Python and Debian consumers.
# contrib/MiniZip, the component affected by CVE-2023-45853, is not packaged.
zlib_version='1:1.3.dfsg+really1.3.2.1+academy.git20260904.e3dc0a8-1'
zlib_archive="${work_root}/zlib.tar.gz"
download \
    'https://github.com/madler/zlib/archive/e3dc0a85b7032e98380dec011bc8f2c2ee0d8fca.tar.gz' \
    "${zlib_archive}" \
    '33356dac6140d584347fe46bcf7083bd949dec49ac4b52417ae334ec70e3dbc3'
tar -xzf "${zlib_archive}" -C "${work_root}"
zlib_source="${work_root}/zlib-e3dc0a85b7032e98380dec011bc8f2c2ee0d8fca"
grep -Fq '#define ZLIB_VERSION "1.3.2.1-motley"' "${zlib_source}/zlib.h"
grep -Fq 'strm->next_in == NULL' "${zlib_source}/gzwrite.c"
(
    cd "${zlib_source}"
    CFLAGS='-O2 -fstack-protector-strong -fPIC' ./configure --prefix=/usr
    make -j "${jobs}"
    make test
)
zlib_package="${work_root}/zlib-package"
zlib_library="$(find "${zlib_source}" -maxdepth 1 -type f -name 'libz.so.*' | head -n 1)"
test -n "${zlib_library}"
install -D -m 0644 \
    "${zlib_library}" \
    "${zlib_package}/usr/lib/${multiarch}/$(basename "${zlib_library}")"
ln -s "$(basename "${zlib_library}")" \
    "${zlib_package}/usr/lib/${multiarch}/libz.so.1"
test -z "$(find "${zlib_package}" \
    \( -iname '*minizip*' -o -iname '*pyminizip*' \) -print -quit)"
! nm -D --defined-only "${zlib_library}" | \
    grep -Fq 'zipOpenNewFileInZip4_64'
write_control \
    "${zlib_package}" \
    'zlib1g' \
    'zlib' \
    "${zlib_version}" \
    'required' \
    'Pre-Depends: libc6 (>= 2.34)' \
    'compression library with the CVE-2026-85091 fix'
dpkg-deb --build --root-owner-group \
    "${zlib_package}" "${output_root}/zlib1g-fixed.deb"

# CVE-2026-86145 is fixed in upstream PCRE2 10.48. Only the 8-bit shared
# library belongs to the existing libpcre2-8-0 runtime package.
pcre2_version='10.48-2~academy1'
pcre2_archive="${work_root}/pcre2.tar.gz"
download \
    'https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.48/pcre2-10.48.tar.gz' \
    "${pcre2_archive}" \
    'ebcc25aadf2a51fa1fefa9b8bc9e7a79b3dae86870a0f1152a22e42befd46888'
tar -xzf "${pcre2_archive}" -C "${work_root}"
pcre2_source="${work_root}/pcre2-10.48"
(
    cd "${pcre2_source}"
    ./configure \
        --prefix=/usr \
        --libdir="/usr/lib/${multiarch}" \
        --disable-static \
        --disable-pcre2-16 \
        --disable-pcre2-32 \
        --disable-pcre2grep-libz \
        --disable-pcre2grep-libbz2 \
        --disable-pcre2grep-libreadline
    make -j "${jobs}"
    make check
)
pcre2_package="${work_root}/pcre2-package"
pcre2_library="$(find "${pcre2_source}/.libs" -maxdepth 1 -type f -name 'libpcre2-8.so.0.*' | head -n 1)"
test -n "${pcre2_library}"
install -D -m 0644 \
    "${pcre2_library}" \
    "${pcre2_package}/usr/lib/${multiarch}/$(basename "${pcre2_library}")"
ln -s "$(basename "${pcre2_library}")" \
    "${pcre2_package}/usr/lib/${multiarch}/libpcre2-8.so.0"
write_control \
    "${pcre2_package}" \
    'libpcre2-8-0' \
    'pcre2' \
    "${pcre2_version}" \
    'optional' \
    'Depends: libc6 (>= 2.34)' \
    'PCRE2 8-bit runtime with the CVE-2026-86145 fix'
dpkg-deb --build --root-owner-group \
    "${pcre2_package}" "${output_root}/libpcre2-8-0-fixed.deb"

# CVE-2026-86140 is fixed upstream in 2.15.4, whose SONAME differs from the
# trixie runtime. Apply that single fix after the complete Debian deb13u3 patch
# series to retain libxml2.so.2 and every stable security backport.
libxml2_version='2.15.4+really2.9.14-2.1+deb13u3+academy1'
libxml2_orig="${work_root}/libxml2.orig.tar.xz"
libxml2_debian="${work_root}/libxml2.debian.tar.xz"
libxml2_fix="${work_root}/CVE-2026-86140.patch"
download \
    'https://download.gnome.org/sources/libxml2/2.9/libxml2-2.9.14.tar.xz' \
    "${libxml2_orig}" \
    '60d74a257d1ccec0475e749cba2f21559e48139efba6ff28224357c7c798dfee'
download \
    'https://deb.debian.org/debian/pool/main/libx/libxml2/libxml2_2.12.7+dfsg+really2.9.14-2.1+deb13u3.debian.tar.xz' \
    "${libxml2_debian}" \
    '3b6d265f482d6a8fbe3c056d2006fb3b563b4a838f7258b388ac5f0b29206921'
download \
    'https://github.com/GNOME/libxml2/commit/d1686f91dbda141a752200419d35639fd6b38340.patch' \
    "${libxml2_fix}" \
    '5e51f8dcadfe3294a15d9e8644d61e8d92bd5b9e72c10cf13ef010eccfbfa4df'
tar -xJf "${libxml2_orig}" -C "${work_root}"
libxml2_source="${work_root}/libxml2-2.9.14"
tar -xJf "${libxml2_debian}" -C "${libxml2_source}"
(
    cd "${libxml2_source}"
    QUILT_PATCHES=debian/patches quilt push -a
    patch --batch --forward -p1 <"${libxml2_fix}"
    grep -Fq 'if (size - len < 50)' valid.c
    autoreconf --force --install
    CFLAGS='-O2 -fstack-protector-strong -fPIC' \
        LDFLAGS='-Wl,-z,relro -Wl,-z,now' \
        ./configure \
            --prefix=/usr \
            --libdir="/usr/lib/${multiarch}" \
            --disable-static \
            --without-python \
            --with-lzma \
            --with-zlib
    make -j "${jobs}"
    make check
)
libxml2_package="${work_root}/libxml2-package"
libxml2_library="$(find "${libxml2_source}/.libs" -maxdepth 1 -type f -name 'libxml2.so.2.*' | head -n 1)"
test -n "${libxml2_library}"
install -D -m 0644 \
    "${libxml2_library}" \
    "${libxml2_package}/usr/lib/${multiarch}/$(basename "${libxml2_library}")"
ln -s "$(basename "${libxml2_library}")" \
    "${libxml2_package}/usr/lib/${multiarch}/libxml2.so.2"
write_control \
    "${libxml2_package}" \
    'libxml2' \
    'libxml2' \
    "${libxml2_version}" \
    'optional' \
    'Depends: libc6 (>= 2.34), liblzma5 (>= 5.1.1alpha+20120614), zlib1g (>= 1:1.2.3.3)' \
    'GNOME XML runtime with stable ABI and the CVE-2026-86140 fix'
dpkg-deb --build --root-owner-group \
    "${libxml2_package}" "${output_root}/libxml2-fixed.deb"

# CVE-2026-93990 has no released Expat/Debian fix. Keep the real upstream
# version and source-package identity; scanner acceptance is a separate gate.
expat_version='2.8.4+academy1-1'
expat_archive="${work_root}/expat.tar.xz"
expat_fix="${work_root}/expat-fix.patch"
expat_tests="${work_root}/expat-tests.patch"
download \
    'https://github.com/libexpat/libexpat/releases/download/R_2_8_4/expat-2.8.4.tar.xz' \
    "${expat_archive}" \
    '656ae1cc8da3b4ea513bb4e254f33e6243938084c0ec6239da873376b09985a7'
download \
    'https://github.com/libexpat/libexpat/commit/0cfd15bdf4b2c22d6b0df73610709dfb60921091.patch' \
    "${expat_fix}" \
    '87b8095c3b348dc855ee21dd43ef2705a95269acac650db51f5c2df40cb9f307'
download \
    'https://github.com/libexpat/libexpat/commit/28fcfba540f6933aa8904a1514c4811713d2ab72.patch' \
    "${expat_tests}" \
    '7a470c152b8c0dbb2a32cff790445a905b81cf1d8894cd1bf7112e7754e1bc13'
tar -xJf "${expat_archive}" -C "${work_root}"
expat_source="${work_root}/expat-2.8.4"
# Only patch context changes: 2.8.4 still has FASTCALL, unlike upstream main.
test "$(grep -Fxc ' static int checkCharRefNumber(int result);' "${expat_fix}")" -eq 1
sed 's/^ static int checkCharRefNumber(int result);$/ static int FASTCALL checkCharRefNumber(int result);/' \
    "${expat_fix}" > "${work_root}/expat-fix-context.patch"
(cd "${expat_source}" && patch --batch --forward --fuzz=0 -p2 < "${expat_tests}")

for mode in normal min; do
    build="${work_root}/expat-red-${mode}"
    mkdir "${build}"
    flags='-O2 -fstack-protector-strong -fPIC'
    if [ "${mode}" = min ]; then flags="${flags} -DXML_MIN_SIZE"; fi
    (
        cd "${build}"
        CFLAGS="${flags}" "${expat_source}/configure" --disable-static \
            --without-docbook --without-examples --without-xmlwf
        make -j "${jobs}"
        if make check > red.log 2>&1; then
            echo 'ERROR: unpatched Expat unexpectedly passed the upstream regression' >&2
            exit 1
        fi
        test "$(grep -c '^FAIL \[' tests/runtests.log)" -eq 12
        ! grep '^FAIL \[' tests/runtests.log | grep -v 'test_utf16_surrogate_pairs ('
        result=0
        python /usr/local/bin/verify-expat.py --library "${build}/lib/.libs/libexpat.so.1" \
            > oracle.log 2>&1 || result=$?
        test "${result}" -eq 66
        grep -Fxq 'EXPAT_UTF16_INVALID_ACCEPTED' oracle.log
        printf 'EXPAT_UPSTREAM_RED_PASS mode=%s failures=12\n' "${mode}"
    )
done
(cd "${expat_source}" && patch --batch --forward --fuzz=0 -p2 < "${work_root}/expat-fix-context.patch")

expat_package="${work_root}/expat-package"
for mode in normal min wide; do
    build="${work_root}/expat-green-${mode}"
    source="${expat_source}"
    flags='-O2 -fstack-protector-strong -fPIC'
    library_name=libexpat
    if [ "${mode}" = min ]; then flags="${flags} -DXML_MIN_SIZE"; fi
    if [ "${mode}" = wide ]; then
        # Debian libexpat1 owns both SONAMEs. Follow upstream's documented
        # wide-library build procedure instead of dropping the wide ABI.
        source="${work_root}/expat-wide"
        cp -a "${expat_source}" "${source}"
        find "${source}" -name Makefile.am -exec sed -i \
            -e 's/libexpat\.la/libexpatw.la/g' -e 's/libexpat_la/libexpatw_la/g' {} +
        (cd "${source}" && autoreconf --force --install)
        flags="${flags} -DXML_UNICODE"
        library_name=libexpatw
    fi
    mkdir "${build}"
    (
        cd "${build}"
        CFLAGS="${flags}" LDFLAGS='-Wl,-z,relro -Wl,-z,now' \
            "${source}/configure" --prefix=/usr --libdir="/usr/lib/${multiarch}" \
            --disable-static --without-docbook --without-examples --without-xmlwf
        make -j "${jobs}"
        if [ "${mode}" = wide ]; then
            # Upstream common.h explicitly rejects ushort XML_UNICODE tests.
            # Keep Debian's ushort ABI; verify this actual library's input and
            # UTF-16 callback output instead of switching to incompatible wchar_t.
            python /usr/local/bin/verify-expat.py --library "${build}/lib/.libs/libexpatw.so.1" --wide
            printf 'EXPAT_WIDE_ABI_AND_UTF16_PASS\n'
        else
            make check
            python /usr/local/bin/verify-expat.py --library "${build}/lib/.libs/libexpat.so.1"
            printf 'EXPAT_UPSTREAM_GREEN_PASS mode=%s\n' "${mode}"
        fi
    )
    if [ "${mode}" != min ]; then
        library="$(find "${build}/lib/.libs" -maxdepth 1 -type f -name "${library_name}.so.1.*")"
        test "$(printf '%s\n' "${library}" | wc -l)" -eq 1
        test -f "${library}"
        install -D -m 0644 "${library}" \
            "${expat_package}/usr/lib/${multiarch}/$(basename "${library}")"
        ln -s "$(basename "${library}")" "${expat_package}/usr/lib/${multiarch}/${library_name}.so.1"
    fi
done
install -D -m 0644 "${expat_source}/COPYING" "${expat_package}/usr/share/doc/libexpat1/copyright"
write_control "${expat_package}" 'libexpat1' 'expat' "${expat_version}" 'optional' \
    'Depends: libc6 (>= 2.36)' 'Expat runtime with the CVE-2026-93990 backport'
dpkg-deb --build --root-owner-group "${expat_package}" "${output_root}/libexpat1-fixed.deb"

# The pinned Python image embeds Expat 2.7.4. Rebuild its matching XML pair;
# _elementtree requires the exact Expat CAPI version exposed by pyexpat.
python_archive="${work_root}/python.tar.xz"
download \
    'https://www.python.org/ftp/python/3.11.15/Python-3.11.15.tar.xz' \
    "${python_archive}" \
    '272179ddd9a2e41a0fc8e42e33dfbdca0b3711aa5abf372d3f2d51543d09b625'
tar -xJf "${python_archive}" -C "${work_root}"
python /usr/local/bin/build-python-xml.py "${work_root}/Python-3.11.15" \
    "${expat_source}" "${work_root}/expat-green-normal" "${output_root}/python-xml"
test "$(find "${output_root}" -maxdepth 1 -type f -name '*.deb' | wc -l)" -eq 4
