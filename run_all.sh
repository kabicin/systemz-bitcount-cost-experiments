#!/usr/bin/env bash
# Run every toy folder (<type>-<op>) and then the C++ examples in cpp/.
# Options go to each run, e.g. ./run_all.sh --cpus z13,z17
# Use your own LLVM checkout with LLVM_PROJECT=/path/to/llvm-project ./run_all.sh
# (tools come from <it>/build/bin; LLVM_BIN overrides that directory).
# The C++ results are also appended to records/cpp-history.csv.
#
#   ./run_all.sh --hw [--cpus z14,z16]
#
# --hw also runs every example on the machine itself, after the llvm-mca
# estimates: the toy folders (toy_on_z.sh) and the C++ examples (cpp/run_on_z.sh).
# Each prints the measured time next to its llvm-mca estimate. It needs IBM Z
# (s390x Linux) with a C compiler (CC=gcc by default; ROUNDS=3 timed rounds), and
# stops at once on any other machine. A -march level newer than the machine is
# reported as not supported. With --no-prebuilt the estimates leave prebuilt/
# alone and the hardware step times the objects already there.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1

HW=0
ARGS=()      # for the estimate scripts
HW_ARGS=()   # for the hardware scripts: everything except --no-prebuilt, which they do not take
for a in "$@"; do
  if [ "$a" = "--hw" ]; then HW=1; else
    ARGS+=("$a")
    [ "$a" = "--no-prebuilt" ] || HW_ARGS+=("$a")
  fi
done

if [ "$HW" = 1 ] && [ "$(uname -m)" != "s390x" ]; then
  echo "error: --hw runs the code on IBM Z, but this machine is $(uname -m)." >&2
  echo "       Run ./run_all.sh --hw on an s390x Linux machine." >&2
  exit 1
fi

for d in *-ct*/; do
  d=${d%/}
  echo "=================== $d"
  python3 bitcount_toy.py "$d" ${ARGS[@]+"${ARGS[@]}"}
  echo
done
echo "=================== cpp"
python3 cpp/run_cpp.py ${ARGS[@]+"${ARGS[@]}"}

if [ "$HW" = 1 ]; then
  echo
  echo "=================== toys on IBM Z hardware (measured, with the llvm-mca estimate)"
  ./toy_on_z.sh ${HW_ARGS[@]+"${HW_ARGS[@]}"}
  echo
  echo "=================== cpp on IBM Z hardware (measured, with the llvm-mca estimate)"
  cpp/run_on_z.sh ${HW_ARGS[@]+"${HW_ARGS[@]}"}
fi
