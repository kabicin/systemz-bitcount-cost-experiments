#!/usr/bin/env bash
# Time the prebuilt upstream and patched code of the toy folders on IBM Z.
#
#   ./toy_on_z.sh                         every toy folder, every -march level this machine can run
#   ./toy_on_z.sh i128-ctlz v8i32-cttz    chosen folders
#   ./toy_on_z.sh --cpus z13,z14          chosen levels only
#   ROUNDS=5 CC=clang ./toy_on_z.sh
#
# Needs only a C compiler (gcc by default) on s390x Linux. For each folder and
# -march level in <folder>/prebuilt/ it links toy_bench.c with the upstream and
# with the patched object, checks every result against reference code, then runs
# the two binaries alternately ROUNDS times (default 3) and takes the median.
# Tests: loop and mix (ns per element), slp2/4/8 (ns per call of a group of
# values). Each is printed next to the llvm-mca estimate for the same test.
#
# Results are printed, written to build-z/toys-results.txt and appended to
# records/hw-history.csv (function column "<folder>/<test>"). A level newer than
# the machine stops with "illegal instruction" and is reported as not supported.
# The objects are written by bitcount_toy.py (also run by run_all.sh).
set -eu
cd "$(dirname "$0")"

CC=${CC:-gcc}
ROUNDS=${ROUNDS:-3}
CPUS=""
FORCE=0
FOLDERS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --cpus) CPUS=$(echo "$2" | tr ',' ' '); shift 2 ;;
    --force) FORCE=1; shift ;;   # skip the s390x check (for testing the script)
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 1 ;;
    *) FOLDERS+=("${1%/}"); shift ;;
  esac
done

if [ "$(uname -m)" != "s390x" ] && [ "$FORCE" = 0 ]; then
  echo "error: this script runs on IBM Z (s390x); this machine is $(uname -m)" >&2
  exit 1
fi
command -v "$CC" >/dev/null || { echo "error: $CC not found (set CC=...)" >&2; exit 1; }
[ ${#FOLDERS[@]} -gt 0 ] || FOLDERS=($(ls -d *-ct*/ | tr -d /))

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

CCVER=$("$CC" --version 2>/dev/null | head -1)
STAMP=$(date +%Y-%m-%dT%H:%M:%S)
mkdir -p build-z
OUT=build-z/toys-results.txt
: > "$OUT"
say() { echo "$*" | tee -a "$OUT"; }

say "IBM Z hardware run of the toy folders: machine type ${MTYPE:-?} (${MNAME}), kernel $(uname -r)"
say "compiler for the driver: $CCVER"
say "rounds: $ROUNDS (upstream and patched alternate; median of per-round medians)"
say "ns are per element (loop, mix) or per call (slpN); the mca estimate is upstream cycles / patched cycles."
say ""

HIST=records/hw-history.csv
mkdir -p records
[ -f "$HIST" ] || echo "timestamp,machine_type,machine,kernel,driver_cc,llvm_commit,patch_fingerprint,march,function,upstream_ns,patched_ns,speedup,mca_speedup" > "$HIST"
ROWS=build-z/toys-rows.csv

for folder in "${FOLDERS[@]}"; do
  pb=$folder/prebuilt
  if [ ! -f "$pb/MANIFEST.txt" ]; then
    say "$folder: no prebuilt/ (run python3 bitcount_toy.py $folder on the build machine)"; say ""; continue
  fi
  manifest() { awk -F': ' -v k="$1" '$1 == k {print $2}' "$pb/MANIFEST.txt"; }
  COMMIT=$(manifest llvm_commit); FP=$(manifest patch_fingerprint)

  # <folder> is [v<lanes>]i<width>-<op>.
  base=${folder##*/}; ty=${base%-*}; op=${base##*-}
  lanes=1; width=${ty##*i}
  case "$ty" in v*) lanes=${ty#v}; lanes=${lanes%%i*} ;; esac
  case "$op" in ctlz) opn=0 ;; cttz) opn=1 ;; ctpop) opn=2 ;; *) echo "error: bad op in $folder" >&2; exit 1 ;; esac
  FLAGS="-DW=$width -DLANES=$lanes -DOP=$opn"
  for k in slp2 slp4 slp8 mix; do
    grep -q "@$k(" "$folder/kernels.ll" && FLAGS="$FLAGS -DHAVE_$(echo "$k" | tr a-z A-Z)"
  done

  cpus=$CPUS
  [ -n "$cpus" ] || cpus=$(cd "$pb" && ls -d z1* 2>/dev/null | tr '\n' ' ')
  say "=================== $folder (prebuilt: LLVM commit $COMMIT, patch $FP, built $(manifest built))"
  : > "$ROWS"
  for cpu in $cpus; do
    for v in upstream patched; do
      [ -f "$pb/$cpu/$v.o" ] || { say "  -march=$cpu: $pb/$cpu/$v.o missing"; continue 2; }
      # shellcheck disable=SC2086
      "$CC" -O2 $FLAGS -o "build-z/toy_${base}_${cpu}_$v" toy_bench.c "$pb/$cpu/$v.o"
    done

    # Correctness and support check: one run of each binary.
    status=ok
    for v in upstream patched; do
      set +e
      "./build-z/toy_${base}_${cpu}_$v" > "build-z/toy_${base}_${cpu}_${v}_r0.txt" 2> "build-z/toy_${base}_${cpu}_${v}_err.txt"
      rc=$?
      set -e
      if [ $rc -eq 132 ] || grep -qi "illegal instruction" "build-z/toy_${base}_${cpu}_${v}_err.txt"; then
        status="not supported on this machine (illegal instruction)"; break
      elif [ $rc -eq 2 ]; then
        status="WRONG RESULTS from $v code: $(head -3 "build-z/toy_${base}_${cpu}_${v}_err.txt" | tr '\n' ' ')"; break
      elif [ $rc -ne 0 ]; then
        status="$v binary failed (exit $rc)"; break
      fi
    done
    if [ "$status" != ok ]; then
      say "  -march=$cpu: $status"
      continue
    fi

    # Timed rounds, alternating the two binaries to spread out machine noise.
    # The order flips every round: with the same fixed order, identical code
    # measured up to 1.4x apart (the binary that runs second tends to look faster).
    for r in $(seq 1 "$ROUNDS"); do
      if [ $((r % 2)) -eq 1 ]; then order="upstream patched"; else order="patched upstream"; fi
      for v in $order; do
        "./build-z/toy_${base}_${cpu}_$v" | awk -v c="$cpu" -v v="$v" '{print c "," $1 "," v "," $2}' >> "$ROWS"
      done
    done
  done

  # Median per (cpu, test, variant), joined with the llvm-mca estimate.
  SUMMARY=build-z/toys-summary.csv
  sort -t, -k1,1 -k2,2 -k3,3 -k4,4g "$ROWS" | awk -F, '
    { key = $1 "," $2 "," $3; v[key, ++n[key]] = $4; if (!(key in seen)) { seen[key] = 1; order[++k] = key } }
    END { for (i = 1; i <= k; i++) { key = order[i]; m = n[key];
            med = (m % 2) ? v[key, (m + 1) / 2] : (v[key, m / 2] + v[key, m / 2 + 1]) / 2;
            print key "," med } }' > "$SUMMARY"
  if [ -s "$SUMMARY" ]; then
    say "$(printf '  %-4s %-6s %13s %13s %9s %14s' cpu test 'upstream ns' 'patched ns' speedup 'mca estimate')"
    awk -F, '$3 == "upstream" {u[$1 "," $2] = $4; ord[++k] = $1 "," $2} $3 == "patched" {p[$1 "," $2] = $4}
             END {for (i = 1; i <= k; i++) print ord[i] "," u[ord[i]] "," p[ord[i]]}' "$SUMMARY" |
    while IFS=, read -r cpu test up pa; do
      est=$(awk -F, -v c="$cpu" -v t="$test" '$1 == c && $2 == t && $3 != "" && $4 > 0 {printf "%.2f", $3 / $4}' "$pb/estimates.csv")
      sp=$(awk -v u="$up" -v p="$pa" 'BEGIN {printf "%.2f", (p > 0) ? u / p : 0}')
      say "$(printf '  %-4s %-6s %13.3f %13.3f %8sx %13sx' "$cpu" "$test" "$up" "$pa" "$sp" "${est:-?}")"
      echo "$STAMP,$MTYPE,$MNAME,$(uname -r),\"$CCVER\",$COMMIT,$FP,$cpu,$folder/$test,$up,$pa,$sp,${est:-}" >> "$HIST"
    done
  fi
  say ""
done
say "speedup = upstream ns / patched ns (above 1 means the patch is faster)."
say "Results appended to records/hw-history.csv; full output in build-z/."
