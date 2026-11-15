#!/usr/bin/env bash
# Time the prebuilt upstream and patched code for examples.cpp on IBM Z.
#
#   cpp/run_on_z.sh                  every -march level this machine can run
#   cpp/run_on_z.sh --cpus z13,z14   chosen levels only
#   ROUNDS=5 CC=clang cpp/run_on_z.sh
#
# Needs only a C compiler (gcc by default) on s390x Linux. For each -march
# level in prebuilt/ it links bench.c with the upstream and with the patched
# object, checks every result against reference code, then runs the two
# binaries alternately ROUNDS times (default 3) and takes the median.
#
# Results are printed, written to cpp/build-z/results.txt and appended to
# records/hw-history.csv, next to the llvm-mca estimate for the same case.
# A level newer than the machine stops with "illegal instruction" and is
# reported as not supported.
set -eu
cd "$(dirname "$0")"

CC=${CC:-gcc}
ROUNDS=${ROUNDS:-3}
CPUS=""
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cpus) CPUS=$(echo "$2" | tr ',' ' '); shift 2 ;;
    --force) FORCE=1; shift ;;   # skip the s390x check (for testing the script)
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

if [ "$(uname -m)" != "s390x" ] && [ "$FORCE" = 0 ]; then
  echo "error: this script runs on IBM Z (s390x); this machine is $(uname -m)" >&2
  exit 1
fi
command -v "$CC" >/dev/null || { echo "error: $CC not found (set CC=...)" >&2; exit 1; }
[ -f prebuilt/MANIFEST.txt ] || { echo "error: prebuilt/ is missing; run cpp/run_cpp.py on the build machine" >&2; exit 1; }
[ -n "$CPUS" ] || CPUS=$(cd prebuilt && ls -d z1* 2>/dev/null | tr '\n' ' ')

# Machine type from /proc/cpuinfo, e.g. "machine = 3931".
MTYPE=$(awk -F'machine = ' '/machine = /{split($2, a, /[ ,]/); print a[1]; exit}' /proc/cpuinfo 2>/dev/null || true)
case "$MTYPE" in
  2964|2965) MNAME=z13 ;;
  3906|3907) MNAME=z14 ;;
  8561|8562) MNAME=z15 ;;
  3931|3932) MNAME=z16 ;;
  9175|9176) MNAME=z17 ;;
  "") MNAME=unknown ;;
  *) MNAME="unknown ($MTYPE)" ;;
esac

manifest() { awk -F': ' -v k="$1" '$1 == k {print $2}' prebuilt/MANIFEST.txt; }
COMMIT=$(manifest llvm_commit)
FP=$(manifest patch_fingerprint)
CCVER=$("$CC" --version 2>/dev/null | head -1)
STAMP=$(date +%Y-%m-%dT%H:%M:%S)
mkdir -p build-z
OUT=build-z/results.txt
: > "$OUT"
say() { echo "$*" | tee -a "$OUT"; }

say "IBM Z hardware run: machine type ${MTYPE:-?} (${MNAME}), kernel $(uname -r)"
say "compiler for the driver: $CCVER"
say "prebuilt code: LLVM commit $COMMIT, patch $FP (built $(manifest built))"
say "rounds: $ROUNDS (upstream and patched alternate; median of per-round medians)"
say ""

ROWS=build-z/rows.csv
: > "$ROWS"
for cpu in $CPUS; do
  for v in upstream patched; do
    obj=prebuilt/$cpu/$v.o
    [ -f "$obj" ] || { echo "error: $obj missing" >&2; exit 1; }
    "$CC" -O2 -o "build-z/bench_${cpu}_$v" bench.c "$obj"
  done

  # Correctness and support check: one run of each binary.
  status=ok
  for v in upstream patched; do
    set +e
    "./build-z/bench_${cpu}_$v" > "build-z/${cpu}_${v}_r0.txt" 2> "build-z/${cpu}_${v}_err.txt"
    rc=$?
    set -e
    if [ $rc -eq 132 ] || grep -qi "illegal instruction" "build-z/${cpu}_${v}_err.txt"; then
      status="not supported on this machine (illegal instruction)"; break
    elif [ $rc -eq 2 ]; then
      status="WRONG RESULTS from $v code: $(head -3 "build-z/${cpu}_${v}_err.txt" | tr '\n' ' ')"; break
    elif [ $rc -ne 0 ]; then
      status="$v binary failed (exit $rc)"; break
    fi
  done
  if [ "$status" != ok ]; then
    say "  -march=$cpu: $status"
    continue
  fi

  # Timed rounds, alternating the two binaries to spread out machine noise.
  for r in $(seq 1 "$ROUNDS"); do
    for v in upstream patched; do
      "./build-z/bench_${cpu}_$v" | awk -v c="$cpu" -v v="$v" '{print c "," $1 "," v "," $2}' >> "$ROWS"
    done
  done
done

# Median per (cpu, function, variant), joined with the llvm-mca estimate.
SUMMARY=build-z/summary.csv
sort -t, -k1,1 -k2,2 -k3,3 -k4,4g "$ROWS" | awk -F, '
  { key = $1 "," $2 "," $3; v[key, ++n[key]] = $4; if (!(key in seen)) { seen[key] = 1; order[++k] = key } }
  END { for (i = 1; i <= k; i++) { key = order[i]; m = n[key];
          med = (m % 2) ? v[key, (m + 1) / 2] : (v[key, m / 2] + v[key, m / 2 + 1]) / 2;
          print key "," med } }' > "$SUMMARY"

if [ -s "$SUMMARY" ]; then
  say "$(printf '  %-4s %-18s %13s %13s %9s %14s' cpu function 'upstream ns' 'patched ns' speedup 'mca estimate')"
  HIST=../records/hw-history.csv
  mkdir -p ../records   # not in a fresh clone until the first results are committed
  [ -f "$HIST" ] || echo "timestamp,machine_type,machine,kernel,driver_cc,llvm_commit,patch_fingerprint,march,function,upstream_ns,patched_ns,speedup,mca_speedup" > "$HIST"
  awk -F, '$3 == "upstream" {u[$1 "," $2] = $4; ord[++k] = $1 "," $2} $3 == "patched" {p[$1 "," $2] = $4}
           END {for (i = 1; i <= k; i++) print ord[i] "," u[ord[i]] "," p[ord[i]]}' "$SUMMARY" |
  while IFS=, read -r cpu fn up pa; do
    est=$(awk -F, -v c="$cpu" -v f="$fn" '$1 == c && $2 == f {print $5}' prebuilt/estimates.csv)
    sp=$(awk -v u="$up" -v p="$pa" 'BEGIN {printf "%.2f", (p > 0) ? u / p : 0}')
    say "$(printf '  %-4s %-18s %13.3f %13.3f %8sx %13sx' "$cpu" "$fn" "$up" "$pa" "$sp" "${est:-?}")"
    echo "$STAMP,$MTYPE,$MNAME,$(uname -r),\"$CCVER\",$COMMIT,$FP,$cpu,$fn,$up,$pa,$sp,${est:-}" >> "$HIST"
  done
  say ""
  say "speedup = upstream ns / patched ns (above 1 means the patch is faster)."
  say "Results appended to records/hw-history.csv; full output in cpp/build-z/."
else
  say "No -march level could be timed on this machine."
fi
