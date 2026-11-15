#!/usr/bin/env bash
# Run every toy folder (iN-op) in this directory. Options go to each run,
# e.g. ./run_all.sh --cpus z13,z17
set -euo pipefail
cd "$(dirname "$0")"
for d in i*-ct*/; do
  d=${d%/}
  echo "=================== $d"
  python3 bitcount_toy.py "$d" "$@"
  echo
done
