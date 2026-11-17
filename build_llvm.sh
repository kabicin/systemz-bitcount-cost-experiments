#!/usr/bin/env bash
# Build the LLVM tools the experiments need, from your llvm-project checkout.
#
#   ./build_llvm.sh                  configure if needed, build opt llc llvm-mca,
#                                    and opt-upstream (see below)
#   ./build_llvm.sh --test           also run the SystemZ cost-model lit tests
#   ./build_llvm.sh --run            run ./run_all.sh when the build is done
#   ./build_llvm.sh --no-upstream    skip opt-upstream
#   ./build_llvm.sh --upstream-ref REF   commit to build opt-upstream from
#                                    (default HEAD~1: the commit before the patch)
#   ./build_llvm.sh --rebuild-upstream   rebuild opt-upstream even if up to date
#   ./build_llvm.sh --reconfigure    rerun cmake (removes build/CMakeCache.txt)
#   ./build_llvm.sh -j N             parallel jobs (default: ninja's choice)
#
# Environment:
#   LLVM_PROJECT   checkout to build (default ../llvm-project); build dir is
#                  $LLVM_PROJECT/build, the same place the experiments read from
#   CC / CXX       host compiler for cmake (default clang / clang++)
#   CMAKE_ARGS     extra arguments for the first cmake run
#
# opt-upstream is the same `opt` built from an earlier commit, so the scripts
# can compare it with the patched build. It needs a clean checkout (the script
# switches to REF, builds, and switches back) and is only rebuilt when REF
# changes. Only the SystemZ target is built, without clang: the C++ examples use
# the clang++ on your PATH as a front end.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
LLVM_PROJECT=${LLVM_PROJECT:-$HERE/../llvm-project}
LLVM_PROJECT=$(cd "${LLVM_PROJECT/#\~/$HOME}" 2>/dev/null && pwd) || { echo "error: LLVM_PROJECT '${LLVM_PROJECT}' not found" >&2; exit 1; }
BUILD=$LLVM_PROJECT/build
UPSTREAM_REF=HEAD~1
DO_UPSTREAM=1 FORCE_UP=0 DO_TEST=0 DO_RUN=0 RECONF=0 JOBS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --test) DO_TEST=1 ;;
    --run) DO_RUN=1 ;;
    --no-upstream) DO_UPSTREAM=0 ;;
    --upstream-ref) UPSTREAM_REF=$2; shift ;;
    --rebuild-upstream) FORCE_UP=1 ;;
    --reconfigure) RECONF=1 ;;
    -j) JOBS=(-j "$2"); shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

for t in cmake ninja git; do
  command -v "$t" >/dev/null || { echo "error: $t not found" >&2; exit 1; }
done
[ -d "$LLVM_PROJECT/llvm" ] || { echo "error: $LLVM_PROJECT is not an llvm-project checkout" >&2; exit 1; }
git() { command git -C "$LLVM_PROJECT" "$@"; }
build() { ninja -C "$BUILD" ${JOBS[@]+"${JOBS[@]}"} "$@"; }

echo "llvm-project: $LLVM_PROJECT ($(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD))"

# 1. Configure (once; later runs reuse build/).
[ "$RECONF" = 1 ] && rm -f "$BUILD/CMakeCache.txt"
if [ ! -f "$BUILD/CMakeCache.txt" ]; then
  echo "== configuring"
  # shellcheck disable=SC2086
  cmake -S "$LLVM_PROJECT/llvm" -B "$BUILD" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_ASSERTIONS=ON \
    -DLLVM_TARGETS_TO_BUILD=SystemZ -DLLVM_ENABLE_PROJECTS= \
    -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_EXAMPLES=OFF \
    -DCMAKE_C_COMPILER="${CC:-clang}" -DCMAKE_CXX_COMPILER="${CXX:-clang++}" ${CMAKE_ARGS:-}
fi

# 2. opt-upstream: opt built from UPSTREAM_REF, then back to where we were.
UP=$BUILD/bin/opt-upstream
STAMP=$BUILD/bin/opt-upstream.commit
if [ "$DO_UPSTREAM" = 1 ]; then
  up_commit=$(git rev-parse "$UPSTREAM_REF^{commit}") || { echo "error: bad --upstream-ref $UPSTREAM_REF" >&2; exit 1; }
  if [ "$FORCE_UP" = 0 ] && [ -x "$UP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$up_commit" ]; then
    echo "== opt-upstream is up to date ($(git rev-parse --short "$up_commit"))"
  elif [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "warning: uncommitted changes in $LLVM_PROJECT, so opt-upstream was NOT rebuilt." >&2
    echo "         Commit or stash them, or use --no-upstream to silence this." >&2
  else
    echo "== building opt-upstream from $(git rev-parse --short "$up_commit") ($UPSTREAM_REF)"
    orig=$(git symbolic-ref --quiet --short HEAD || git rev-parse HEAD)
    trap 'git checkout -q "$orig"' EXIT
    git checkout -q "$up_commit"
    build opt
    cp "$BUILD/bin/opt" "$UP"
    echo "$up_commit" > "$STAMP"
    git checkout -q "$orig"
    trap - EXIT
  fi
fi

# 3. The tools the experiments call, from the current checkout.
echo "== building opt llc llvm-mca"
build opt llc llvm-mca

# 4. Optional: lit tests for the SystemZ cost model.
if [ "$DO_TEST" = 1 ]; then
  echo "== building test tools"
  build llvm-config llvm-readobj llvm-objdump llvm-dis llvm-as llvm-symbolizer FileCheck not count split-file
  echo "== lit: Analysis/CostModel/SystemZ"
  "$BUILD/bin/llvm-lit" -sv "$LLVM_PROJECT/llvm/test/Analysis/CostModel/SystemZ"
fi

echo "== done"
ls -l "$BUILD/bin/opt" "$BUILD/bin/llc" "$BUILD/bin/llvm-mca" "$UP" 2>/dev/null | awk '{print "  " $NF}'

if [ "$DO_RUN" = 1 ]; then
  LLVM_PROJECT=$LLVM_PROJECT exec "$HERE/run_all.sh"
fi
