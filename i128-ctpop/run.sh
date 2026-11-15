#!/usr/bin/env bash
# Measure this toy. Options are passed through, e.g. ./run.sh --cpus z13,z17
exec python3 "$(dirname "$0")/../bitcount_toy.py" "$(dirname "$0")" "$@"
