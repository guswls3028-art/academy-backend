#!/bin/sh
set -eu

# DSA-6607-1: the pinned Python image still has APT 3.0.3. Disable download
# retries until the official stable fix is installed and verified.
apt-get -o Acquire::Retries=0 update
apt-get -o Acquire::Retries=0 install -y --no-install-recommends \
    apt=3.0.4 libapt-pkg7.0=3.0.4
for package in apt libapt-pkg7.0; do
    test "$(dpkg-query -W -f='${Version}' "$package")" = '3.0.4'
    test "$(dpkg-query -W -f='${source:Package}' "$package")" = 'apt'
done
apt-get check
