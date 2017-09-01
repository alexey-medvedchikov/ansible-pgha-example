#!/bin/sh

PATH='/bin:/sbin:/usr/bin:/usr/sbin'

SCRIPT_DIR="$(dirname "$0")"

for file in $SCRIPT_DIR/../.vault_password $SCRIPT_DIR/../.gitlab-ci-secrets.sh
do
    gpg --decrypt $file.gpg
    gpg --batch --yes \
        -r user@domain.tld \
        --encrypt $file
done

