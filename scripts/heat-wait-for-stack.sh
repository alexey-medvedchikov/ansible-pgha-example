#!/bin/bash

function die() {
    echo -e "ERROR: $*" >&2
    exit 1
}

function heat_wait_for_status() {
    local stack_name=$1
    local expected_status=${2:-"CREATE_COMPLETE"}
    local actual_status=
    local polling_delay=10

    echo "Wait \"$stack_name\" for status \"$expected_status\""
    while true
        do
            actual_status=$(heat stack-list | grep $stack_name | awk '{print $6}')
            if [[ $expected_status = $actual_status ]]; then
                echo "Status \"$actual_status\" is expected. Success."
                break
            elif [[ $actual_status =~ '_IN_PROGRESS' ]]; then
                echo "Status is \"$actual_status\". Next polling in $polling_delay sec."
                sleep $polling_delay
            elif [[ $actual_status =~ '_FAILED' ]]; then
                die "Status is \"$actual_status\"."
            else
                die "Can't find any status for stack \"$stack_name\"."
            fi
        done
}

heat_wait_for_status "$@"
