#!/usr/bin/env bash
# Run everything on IBM Z: the toy folders (toy_on_z.sh), then the C++ examples
# (cpp/run_on_z.sh). Each result is printed next to its llvm-mca estimate.
#
#   ./run_on_z.sh                       all toys and the C++ examples
#   ./run_on_z.sh --cpus z14,z16        chosen -march levels only
#   ./run_on_z.sh i128-ctlz v8i32-cttz  chosen toy folders only (skips the C++ examples)
#   ROUNDS=5 CC=clang ./run_on_z.sh
#
# Needs only a C compiler (gcc by default) on s390x Linux: no LLVM and no Python.
# It times the objects in <folder>/prebuilt/ and cpp/prebuilt/, built on another
# machine with ./run_all.sh and copied over with the repo. Results go to build-z/,
# cpp/build-z/ and records/hw-history.csv. Both parts run even if one fails; the
# exit status is non-zero if either did.
set -u
cd "$(dirname "$0")"

OPTS=()      # options for both parts
FOLDERS=()   # toy folders; if any are given, only the toys run
while [ $# -gt 0 ]; do
  case "$1" in
    --cpus) OPTS+=("$1" "$2"); shift 2 ;;
    --force) OPTS+=("$1"); shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 1 ;;
    *) FOLDERS+=("$1"); shift ;;
  esac
done

if [ "$(uname -m)" != "s390x" ] && ! printf '%s\n' ${OPTS[@]+"${OPTS[@]}"} | grep -qx -- --force; then
  echo "error: this script runs on IBM Z (s390x); this machine is $(uname -m)" >&2
  exit 1
fi

rc=0
echo "=================== toys"
./toy_on_z.sh ${OPTS[@]+"${OPTS[@]}"} ${FOLDERS[@]+"${FOLDERS[@]}"} || rc=1
if [ ${#FOLDERS[@]} -eq 0 ]; then
  echo
  echo "=================== cpp"
  cpp/run_on_z.sh ${OPTS[@]+"${OPTS[@]}"} || rc=1
fi
exit $rc
