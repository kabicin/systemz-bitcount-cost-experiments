#!/usr/bin/env bash
# Run everything on IBM Z: the toy folders (toy_on_z.sh), then the C++ examples
# (cpp/run_on_z.sh). Each result is printed next to its llvm-mca estimate. At the
# end the results are packed into export-z/<machine>-<time>/ and a .tar.gz of it,
# to copy to a machine with Python and turn into bitcount-improvements.xlsx.
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
# exit status is non-zero if either did. The export folder holds only this run's
# rows, so the same file can be sent from several machines and merged.
set -u
cd "$(dirname "$0")"

OPTS=()      # options for both parts
FOLDERS=()   # toy folders; if any are given, only the toys run
while [ $# -gt 0 ]; do
  case "$1" in
    --cpus) OPTS+=("$1" "$2"); shift 2 ;;
    --force) OPTS+=("$1"); shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 1 ;;
    *) FOLDERS+=("$1"); shift ;;
  esac
done

if [ "$(uname -m)" != "s390x" ] && ! printf '%s\n' ${OPTS[@]+"${OPTS[@]}"} | grep -qx -- --force; then
  echo "error: this script runs on IBM Z (s390x); this machine is $(uname -m)" >&2
  exit 1
fi

HIST=records/hw-history.csv
before=0
[ -f "$HIST" ] && before=$(wc -l < "$HIST" | tr -d ' ')
rm -f build-z/toys-results.txt cpp/build-z/results.txt   # so a skipped part is not exported stale

rc=0
echo "=================== toys"
./toy_on_z.sh ${OPTS[@]+"${OPTS[@]}"} ${FOLDERS[@]+"${FOLDERS[@]}"} || rc=1
if [ ${#FOLDERS[@]} -eq 0 ]; then
  echo
  echo "=================== cpp"
  cpp/run_on_z.sh ${OPTS[@]+"${OPTS[@]}"} || rc=1
fi

# ---- Export: this run's rows, the logs and the report script, in one folder.
echo
echo "=================== export"
start=$((before + 1)); [ "$before" -gt 0 ] || start=2   # skip the header the first time
NEW=$(mktemp)
[ -f "$HIST" ] && tail -n +"$start" "$HIST" > "$NEW"
rows=$(wc -l < "$NEW" | tr -d ' ')

mname=$(awk -F, 'NR == 1 {print $3}' "$NEW" | tr -c 'A-Za-z0-9\n' '_')
[ -n "$mname" ] || mname=$(hostname -s 2>/dev/null || echo unknown)
name="$mname-$(date +%Y%m%d-%H%M%S)"
dir=export-z/$name
mkdir -p "$dir"

{ head -1 "$HIST" 2>/dev/null || echo "timestamp,machine_type,machine,kernel,driver_cc,llvm_commit,patch_fingerprint,march,function,upstream_ns,patched_ns,speedup,mca_speedup"
  cat "$NEW"; } > "$dir/hw-history.csv"
rm -f "$NEW"
[ -f build-z/toys-results.txt ] && cp build-z/toys-results.txt "$dir/"
[ -f cpp/build-z/results.txt ] && cp cpp/build-z/results.txt "$dir/cpp-results.txt"
[ -f make_report.py ] && cp make_report.py "$dir/"

{
  echo "exported: $(date +%Y-%m-%dT%H:%M:%S)"
  echo "host: $(hostname 2>/dev/null)"
  echo "machine: $(awk -F, 'NR == 2 {print $2 " (" $3 ")"}' "$dir/hw-history.csv")"
  grep -m1 machine /proc/cpuinfo 2>/dev/null | sed 's/^/cpuinfo: /'
  echo "kernel: $(uname -srv)"
  echo "compiler: $(${CC:-gcc} --version 2>/dev/null | head -1)"
  echo "rounds: ${ROUNDS:-3}"
  echo "options: ${OPTS[*]-} ${FOLDERS[*]-}"
  echo "repo_commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "llvm_commit: $(awk -F, 'NR == 2 {print $6}' "$dir/hw-history.csv")"
  echo "patch_fingerprint: $(awk -F, 'NR == 2 {print $7}' "$dir/hw-history.csv")"
  echo "rows: $rows"
  echo "exit_status: $rc"
} > "$dir/meta.txt"

cat > "$dir/README.txt" <<'EOF'
Hardware results from run_on_z.sh (see meta.txt for the machine).

On a machine with Python 3:

    python3 make_report.py hw-history.csv      # writes bitcount-improvements.xlsx
    pip install openpyxl                       # if it says openpyxl is missing

Without openpyxl the script still writes hardware.csv, which opens in any
spreadsheet. Run it from the repo instead to also get the llvm-mca sheets:

    python3 make_report.py export-z/<this file>.tar.gz

hw-history.csv holds this run's rows only: speedup = upstream ns / patched ns,
mca_speedup = the llvm-mca estimate. toys-results.txt and cpp-results.txt are
the printed tables.
EOF

tar -czf "export-z/$name.tar.gz" -C export-z "$name"
if [ "$rows" -eq 0 ]; then
  echo "warning: no timing rows were recorded (see the messages above); the export only has the logs"
fi
echo "exported $rows rows to $dir/ and export-z/$name.tar.gz"
echo "copy it with:  scp $(id -un)@$(hostname -f 2>/dev/null || hostname):$PWD/export-z/$name.tar.gz ."
echo "then:          tar xzf $name.tar.gz && cd $name && python3 make_report.py hw-history.csv"
exit $rc
