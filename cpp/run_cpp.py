#!/usr/bin/env python3
"""Compile examples.cpp for IBM Z and measure it with upstream and patched LLVM.

    cpp/run_cpp.py                     all CPUs (z13..z17)
    cpp/run_cpp.py --cpus z13,z17
    cpp/run_cpp.py --source other.cpp  any C++ file whose extern "C" functions
                                       should be measured

For each CPU:
  1. clang++ turns the C++ into LLVM IR for s390x (-march=<cpu>) without
     running any LLVM optimization (-O2 -Xclang -disable-llvm-passes), so the
     front end only parses and lowers the code.
  2. opt-upstream and opt (the patched build) optimize that IR at -O2.
  3. llc generates the IBM Z assembly.
  4. Each extern "C" function is timed with llvm-mca's model of the CPU.

The LLVM build is $LLVM_PROJECT/build/bin (default ../llvm-project; $LLVM_BIN
overrides the tools directory). clang++ is taken from $CLANG, else from that
build if it has one, else from PATH. Only its front end is used; every
optimization and code-generation step is the LLVM build being tested.

Results are printed, written to cpp/out/results.txt, and appended to
records/cpp-history.csv together with the LLVM commit and a fingerprint of the
uncommitted patch, so runs on different patch versions can be compared.
Cycles are llvm-mca estimates (no cache misses, perfect branch prediction),
not timings on hardware.
"""
import argparse
import csv
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import bitcount_toy as bt  # noqa: E402  (shared parsing and llvm-mca helpers)

sys.dont_write_bytecode = True
LLVM_SRC = os.path.expanduser(os.environ.get("LLVM_SRC", bt.LLVM_PROJECT))
TRIPLE = "s390x-linux-gnu"
CPUS = ["z13", "z14", "z15", "z16", "z17"]
HISTORY = os.path.join(ROOT, "records", "cpp-history.csv")
PREBUILT = os.path.join(HERE, "prebuilt")  # tracked: objects for run_on_z.sh
FIELDS = ["timestamp", "llvm_commit", "patch_fingerprint", "clang", "source",
          "cpu", "function", "upstream_instr", "upstream_cycles",
          "patched_instr", "patched_cycles", "speedup"]


def find_clang():
    for c in (os.environ.get("CLANG"), os.path.join(bt.BIN, "clang++")):
        if c and os.access(c, os.X_OK):
            return c
    c = shutil.which("clang++")
    if not c:
        sys.exit("error: no clang++ found (set CLANG=/path/to/clang++)")
    return c


def git(*args):
    r = subprocess.run(["git", "-C", LLVM_SRC, *args], capture_output=True)
    return r.stdout.decode().strip() if r.returncode == 0 else ""


def patch_fingerprint():
    """Short hash of the uncommitted changes in the LLVM checkout ('clean' if none)."""
    diff = git("diff", "HEAD")
    return hashlib.sha1(diff.encode()).hexdigest()[:10] if diff else "clean"


def extern_c_functions(src):
    """Names of the extern "C" functions defined in the source file."""
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", open(src).read(), flags=re.S)  # drop comments
    return re.findall(r'extern\s+"C"\s+[^;{(]*?\b([A-Za-z_]\w*)\s*\(', text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cpus", default=",".join(CPUS))
    ap.add_argument("--source", default=os.path.join(HERE, "examples.cpp"))
    ap.add_argument("--no-prebuilt", action="store_true",
                    help="do not refresh cpp/prebuilt/ (the objects run_on_z.sh uses)")
    a = ap.parse_args()

    clang = find_clang()
    for t in (bt.OPT, bt.OPT_UP, bt.LLC, bt.MCA):
        if not os.access(t, os.X_OK):
            sys.exit("error: %s not found (set LLVM_BIN)" % t)
    src = os.path.abspath(a.source)
    fns = extern_c_functions(src)
    if not fns:
        sys.exit('error: no extern "C" functions found in %s' % src)
    cpus = a.cpus.split(",")
    out = os.path.join(HERE, "out")
    os.makedirs(out, exist_ok=True)
    clang_ver = subprocess.run([clang, "--version"], capture_output=True).stdout.decode().splitlines()[0]
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    fp = patch_fingerprint()
    stamp = datetime.datetime.now().isoformat(timespec="seconds")

    report, rows = [], []

    def say(s=""):
        print(s)
        report.append(s)

    say("C++ examples on IBM Z: %s" % os.path.relpath(src, ROOT))
    say("front end: %s (%s)" % (clang_ver, clang))
    say("LLVM: commit %s, uncommitted patch %s; upstream = %s, patched = %s"
        % (commit, fp, os.path.basename(bt.OPT_UP), os.path.basename(bt.OPT)))
    say("cycles: llvm-mca estimates per call (per loop trip for functions with a loop)\n")

    results = {}
    for cpu in cpus:
        ll = os.path.join(out, "%s.ll" % cpu)
        r = subprocess.run([clang, "-target", TRIPLE, "-march=" + cpu, "-O2", "-Xclang", "-disable-llvm-passes",
                            "-ffreestanding", "-S", "-emit-llvm", src, "-o", ll], capture_output=True)
        if r.returncode:
            sys.exit("error: clang++ failed:\n" + r.stderr.decode())
        for kind, opt in (("upstream", bt.OPT_UP), ("patched", bt.OPT)):
            bc = bt.run([opt, "-O2", ll, "-o", "-"])
            text = bt.run([bt.LLC, "-o", "-"], stdin=bc).decode()
            with open(os.path.join(out, "%s.%s.s" % (cpu, kind)), "w") as f:
                f.write(text)
            # The same code as an object file, for linking on IBM Z hardware.
            if not a.no_prebuilt:
                d = os.path.join(PREBUILT, cpu)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, kind + ".o"), "wb") as f:
                    f.write(bt.run([bt.LLC, "-filetype=obj", "-o", "-"], stdin=bc))
                with open(os.path.join(d, kind + ".s"), "w") as f:
                    f.write(text)
            for fn in fns:
                body = bt.body(text, fn)
                loop = [i for inl, i in body if inl]
                ins = loop or [i for _, i in body]
                cyc = bt.mca(ins, cpu, os.path.join(out, "%s.%s.%s.s" % (cpu, fn, kind))) if ins else 0.0
                results[(cpu, fn, kind)] = (len([i for _, i in body]), cyc)

    w = max(len(f) for f in fns)
    say("  %-*s %-4s %16s %16s  %s" % (w, "function", "cpu", "upstream", "patched", "effect"))
    for fn in fns:
        for cpu in cpus:
            iu, cu = results[(cpu, fn, "upstream")]
            ip, cp = results[(cpu, fn, "patched")]
            sp = cu / cp if cp else 0.0
            if abs(cu - cp) < 0.01:
                eff = "same" if iu == ip else "same speed, different code"
            else:
                eff = "%.1fx %s" % (max(sp, 1 / sp), "faster" if sp > 1 else "SLOWER")
            say("  %-*s %-4s %16s %16s  %s" % (w, fn, cpu, "%d instr %.2fc" % (iu, cu), "%d instr %.2fc" % (ip, cp), eff))
            rows.append(dict(timestamp=stamp, llvm_commit=commit, patch_fingerprint=fp, clang=clang_ver,
                             source=os.path.relpath(src, ROOT), cpu=cpu, function=fn,
                             upstream_instr=iu, upstream_cycles="%.2f" % cu, patched_instr=ip,
                             patched_cycles="%.2f" % cp, speedup="%.2f" % sp))
        say()

    faster = sum(1 for r in rows if float(r["speedup"]) > 1.005)
    slower = sum(1 for r in rows if 0 < float(r["speedup"]) < 0.995)
    say("summary: %d function/CPU pairs, %d faster, %d slower, %d unchanged" % (len(rows), faster, slower,
                                                                             len(rows) - faster - slower))
    with open(os.path.join(out, "results.txt"), "w") as f:
        f.write("\n".join(report) + "\n")

    if not a.no_prebuilt:
        if os.path.basename(src) != "examples.cpp":
            print("note: cpp/prebuilt/ now holds %s; bench.c only knows the examples.cpp functions"
                  % os.path.basename(src))
        with open(os.path.join(PREBUILT, "MANIFEST.txt"), "w") as f:
            f.write("# Written by cpp/run_cpp.py; read by cpp/run_on_z.sh.\n")
            f.write("built: %s\nllvm_commit: %s\npatch_fingerprint: %s\nclang: %s\nsource: %s\ncpus: %s\n"
                    % (stamp, commit, fp, clang_ver, os.path.relpath(src, ROOT), " ".join(cpus)))
            f.write("upstream: %s\npatched: %s\n" % (os.path.basename(bt.OPT_UP), os.path.basename(bt.OPT)))
        with open(os.path.join(PREBUILT, "estimates.csv"), "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["cpu", "function", "upstream_cycles", "patched_cycles", "mca_speedup"])
            for r in rows:
                wr.writerow([r["cpu"], r["function"], r["upstream_cycles"], r["patched_cycles"], r["speedup"]])
        print("Refreshed cpp/prebuilt/ (objects for cpp/run_on_z.sh on IBM Z hardware)")

    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    new = not os.path.exists(HISTORY)
    with open(HISTORY, "a", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            wr.writeheader()
        wr.writerows(rows)
    print("\nSaved to %s and appended %d rows to %s" % (os.path.relpath(os.path.join(out, "results.txt"), ROOT),
                                                      len(rows), os.path.relpath(HISTORY, ROOT)))


if __name__ == "__main__":
    main()
