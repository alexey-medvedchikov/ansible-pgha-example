#!/usr/bin/env bash

SCRIPT_DIR="$(dirname "$0")"

ansible-galaxy install -r $SCRIPT_DIR/../requirements.yml \
    -p $SCRIPT_DIR/../roles \
    --force
