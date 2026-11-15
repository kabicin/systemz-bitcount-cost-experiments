#!/usr/bin/env bash
# Run every toy folder (<type>-<op>) and then the C++ examples in cpp/.
# Options go to each run, e.g. ./run_all.sh --cpus z13,z17
# The C++ results are also appended to records/cpp-history.csv.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
for d in *-ct*/; do
  d=${d%/}
  echo "=================== $d"
  python3 bitcount_toy.py "$d" "$@"
  echo
done
echo "=================== cpp"
python3 cpp/run_cpp.py "$@"
