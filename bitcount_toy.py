#!/usr/bin/env python3
"""Measure one bit-count toy folder (made by new_toy.py) on IBM Z.

    ./bitcount_toy.py i128-ctlz              (or i128-ctlz/run.sh)
    ./bitcount_toy.py i64-cttz --cpus z13,z17
    ./bitcount_toy.py i64-cttz --no-prebuilt   (do not refresh <folder>/prebuilt/)
    LLVM_PROJECT=~/code/llvm-project ./bitcount_toy.py i64-cttz   (your own checkout)

The folder name gives the type and intrinsic. For each CPU it reports:
  1. the cost opt reports (upstream and patched) vs. instructions emitted
  2. cycles per element in the loop, i.e. what each generation's hardware buys
  3. the patched cost vs. the instructions per element inside the loop
  4. the intrinsic vs. the half-width alternative (@split), upstream and patched
  5. N values loaded from memory (@slp2/4/8), upstream vs patched
  6. a mixed-width loop (@mix), upstream vs patched
  7. which functions the patched opt changes

Cycles come from llvm-mca's model of each CPU: a static model with no cache
misses and perfect branch prediction. Compare rows; it is not wall-clock time.
Results are printed and saved to <folder>/out/results.txt.

It also writes <folder>/prebuilt/: the upstream and patched kernels as object
files for each CPU, and the llvm-mca estimates for @loop, @slpN and @mix in the
units that toy_on_z.sh measures on IBM Z, so the two can be compared.
"""
import argparse
import csv
import datetime
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Your llvm-project checkout: set LLVM_PROJECT=/path/to/llvm-project (tools are
# taken from <it>/build/bin). LLVM_BIN overrides the tools directory directly.
LLVM_PROJECT = os.path.expanduser(os.environ.get("LLVM_PROJECT", os.path.join(HERE, "..", "llvm-project")))
BIN = os.path.expanduser(os.environ.get("LLVM_BIN", os.path.join(LLVM_PROJECT, "build", "bin")))
OPT = os.environ.get("OPT", os.path.join(BIN, "opt"))
OPT_UP = os.environ.get("OPT_UPSTREAM", os.path.join(BIN, "opt-upstream"))
LLC = os.environ.get("LLC", os.path.join(BIN, "llc"))
MCA = os.environ.get("LLVM_MCA", os.path.join(BIN, "llvm-mca"))
TRIPLE = "s390x-linux-gnu"

INSN = re.compile(r"^\t([a-z][a-z0-9]*)\b")
LABEL = re.compile(r"^(\.LBB\d+_\d+:|# %bb\.\d+:)")
# Bytes written by each store mnemonic, used to count elements per loop trip.
STORE_BYTES = {"vst": 16, "vsteg": 8, "vstef": 4, "vsteh": 2, "vsteb": 1,
               "stg": 8, "st": 4, "sty": 4, "sth": 2, "sthy": 2, "stc": 1, "stcy": 1,
               "stmg": 0}
LOADS = re.compile(r"^(vl|vlrep[bhfg]|vlreph|vlrepf|vlrepg|vlrepb|vle[bhfg]|lg|l|ly|llgf|llgh|llgc|lgf|lgh|lgb|llh|llc|lh|lb)$")
OVERHEAD = re.compile(r"^(la|lay|aghi|agfi|brctg|brct|j[a-z]*|c[gl]?[gr]?[ir]?j[a-z]*|cgij[a-z]*|cij[a-z]*|cgrj[a-z]*|clgij[a-z]*)$")


def mnem(i):
    return i.split()[0]


def run(cmd, stdin=None, want="stdout"):
    r = subprocess.run(cmd, input=stdin, capture_output=True)
    if r.returncode:
        sys.exit("error: %s\n%s" % (" ".join(cmd), r.stderr.decode(errors="replace")))
    return getattr(r, want)


def asm(ll, cpu, opt=None):
    if opt:
        bc = run([opt, "-mtriple=" + TRIPLE, "-mcpu=" + cpu, "-O2", ll, "-o", "-"])
        return run([LLC, "-mtriple=" + TRIPLE, "-mcpu=" + cpu, "-o", "-"], stdin=bc).decode()
    return run([LLC, "-mtriple=" + TRIPLE, "-mcpu=" + cpu, ll, "-o", "-"]).decode()


def llvm_git(*args):
    r = subprocess.run(["git", "-C", LLVM_PROJECT, *args], capture_output=True)
    return r.stdout.decode().strip() if r.returncode == 0 else ""


def patch_fingerprint():
    """Short hash of the uncommitted changes in the LLVM checkout ('clean' if none)."""
    import hashlib
    diff = llvm_git("diff", "HEAD")
    return hashlib.sha1(diff.encode()).hexdigest()[:10] if diff else "clean"


def body(text, fn):
    """[(in_main_loop, insn)] for fn. The main loop is the first loop header
    in the function and the blocks that name it as their header."""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith(fn + ":"))
    except StopIteration:
        return []
    out, label, hdr, cur = [], None, None, False
    for l in lines[start + 1:]:
        if l.startswith(".Lfunc_end"):
            break
        if LABEL.match(l):
            label, cur = LABEL.match(l).group(1).rstrip(":").lstrip(".L"), False
            continue
        s = l.strip()
        if "Inner Loop Header" in s and hdr is None:
            hdr, cur = label, True
        h = re.search(r"in Loop: Header=(BB\d+_\d+)", s)
        if h:
            cur = h.group(1) == hdr
        if INSN.match(l) and not s.startswith("br\t%r14"):
            out.append((cur, l.split("#")[0].rstrip()))
    return out


def mca(insns, cpu, path):
    with open(path, "w") as f:
        f.write("\n".join(insns) + "\n")
    t = run([MCA, "-mtriple=" + TRIPLE, "-mcpu=" + cpu, "-iterations=300", path]).decode()
    return int(re.search(r"Total Cycles:\s+(\d+)", t).group(1)) / int(re.search(r"Iterations:\s+(\d+)", t).group(1))


def cost(ll, cpu, opt, ty, op):
    t = run([opt, "-mtriple=" + TRIPLE, "-mcpu=" + cpu, "-passes=print<cost-model>",
             "-disable-output", ll], want="stderr").decode()
    sect = t.split("function 'intrinsic'")[1]
    return int(re.search(r"cost of (\d+) for instruction:\s+%%r = call %s @llvm\.%s" % (re.escape(ty), op),
                         sect).group(1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--cpus", default="z13,z14,z15,z16,z17")
    ap.add_argument("--no-prebuilt", action="store_true",
                    help="do not refresh <folder>/prebuilt/ (the objects toy_on_z.sh uses)")
    a = ap.parse_args()

    folder = os.path.abspath(a.folder)
    m = re.match(r"^(?:v(\d+))?i(\d+)-(ctlz|cttz|ctpop)$", os.path.basename(folder))
    if not m:
        sys.exit("error: folder must be named like i128-ctlz or v8i32-ctpop (see new_toy.py)")
    lanes, width, op = (int(m.group(1)) if m.group(1) else None), int(m.group(2)), m.group(3)
    ty = "i%d" % width if lanes is None else "<%d x i%d>" % (lanes, width)
    elem_bytes = (lanes or 1) * width // 8
    is_vec = lanes is not None
    ll = os.path.join(folder, "kernels.ll")
    for t in (OPT, LLC, MCA, ll):
        if not os.path.exists(t):
            sys.exit("error: %s not found (set LLVM_PROJECT or LLVM_BIN, or run new_toy.py)" % t)
    have_up = os.access(OPT_UP, os.X_OK)
    cpus = a.cpus.split(",")
    out = os.path.join(folder, "out")
    os.makedirs(out, exist_ok=True)
    report = []

    def say(s=""):
        print(s)
        report.append(s)

    rows = {}
    est = []  # [cpu, test, upstream cycles, patched cycles] for toy_on_z.sh
    for cpu in cpus:
        texts = {"patched": asm(ll, cpu, OPT)}
        if have_up:
            texts["upstream"] = asm(ll, cpu, OPT_UP)
        for k, t in texts.items():
            with open(os.path.join(out, "%s.%s.s" % (cpu, k)), "w") as f:
                f.write(t)
        text = texts["patched"]
        intr = [i for _, i in body(text, "intrinsic")]
        # i128 is passed and returned in memory: leave that vl/vst out of the count.
        n = sum(1 for i in intr if not (width == 128 and mnem(i) in ("vl", "vst")))
        loop = [i for inl, i in body(text, "loop") if inl]
        stored = sum(STORE_BYTES.get(mnem(i), 0) for i in loop)
        elems = max(1, stored // max(1, elem_bytes))
        work = [i for i in loop if not (mnem(i) in STORE_BYTES or LOADS.match(mnem(i)) or OVERHEAD.match(mnem(i)))]
        split = {k: [i for _, i in body(t, "split")] for k, t in texts.items()}
        r = dict(
            n=n,
            c_intr=mca(intr, cpu, os.path.join(out, cpu + ".intrinsic.s")),
            elems=elems,
            work=len(work) / elems,
            c_loop=mca(loop, cpu, os.path.join(out, cpu + ".loop.s")) / elems,
            # The loop vectorizer packed several scalars into a vector register.
            vec=not is_vec and width < 128 and any(mnem(i) == "vst" for i in loop),
            up=cost(ll, cpu, OPT_UP, ty, op) if have_up else None,
            pa=cost(ll, cpu, OPT, ty, op),
        )
        # The same split compiled by llc alone. At -O2, InstCombine or SLP may
        # turn the split back into the wide intrinsic, so this column shows what
        # a split *lowering* in the backend would give.
        split["llc"] = [i for _, i in body(asm(ll, cpu), "split")]
        for k, ins in split.items():
            if ins:
                r["c_split_" + k] = mca(ins, cpu, os.path.join(out, "%s.split.%s.s" % (cpu, k)))
        # SLP groups (@slp2/4/8) and the mixed-width loop (@mix), upstream vs patched.
        r["slp"] = {}
        for nvals in (2, 4, 8):
            fn = "slp%d" % nvals
            if not body(text, fn):
                continue
            d = {}
            for k, t in texts.items():
                ins = [i for _, i in body(t, fn)]
                d[k] = (len(ins), mca(ins, cpu, os.path.join(out, "%s.%s.%s.s" % (cpu, fn, k))),
                        any(mnem(i).startswith("v") for i in ins))
            r["slp"][nvals] = d
        r["mix"] = {}
        if body(text, "mix"):
            ow = 64 if width < 64 else 32
            for k, t in texts.items():
                lp = [i for inl, i in body(t, "mix") if inl]
                el = max(1, sum(STORE_BYTES.get(mnem(i), 0) for i in lp) // (ow // 8))
                r["mix"][k] = (el, mca(lp, cpu, os.path.join(out, "%s.mix.%s.s" % (cpu, k))) / el)
        fns = ["intrinsic", "split", "loop"] + ["slp%d" % n for n in r["slp"]] + (["mix"] if r["mix"] else [])
        r["fns"] = [f for f in fns if body(text, f)]
        if have_up:
            r["same"] = {fn: [i for _, i in body(texts["upstream"], fn)] == [i for _, i in body(text, fn)]
                         for fn in r["fns"]}
        rows[cpu] = r
        if not a.no_prebuilt:
            # The same code as object files for linking on IBM Z, and the llvm-mca
            # estimate for each test in the unit toy_bench.c measures: cycles per
            # element (loop, mix) or per call (slpN).
            pd = os.path.join(folder, "prebuilt", cpu)
            os.makedirs(pd, exist_ok=True)
            for k, opt in (("upstream", OPT_UP), ("patched", OPT)):
                if k in texts:
                    bc = run([opt, "-mtriple=" + TRIPLE, "-mcpu=" + cpu, "-O2", ll, "-o", "-"])
                    with open(os.path.join(pd, k + ".o"), "wb") as f:
                        f.write(run([LLC, "-mtriple=" + TRIPLE, "-mcpu=" + cpu, "-filetype=obj", "-o", "-"], stdin=bc))
            cyc = {}
            for k, t in texts.items():
                lp = [i for inl, i in body(t, "loop") if inl]
                el = max(1, sum(STORE_BYTES.get(mnem(i), 0) for i in lp) // max(1, elem_bytes))
                cyc[k, "loop"] = mca(lp, cpu, os.path.join(out, "%s.loop.%s.s" % (cpu, k))) / el
                for n in r["slp"]:
                    cyc[k, "slp%d" % n] = r["slp"][n][k][1]
                if k in r["mix"]:
                    cyc[k, "mix"] = r["mix"][k][1]
            for test in ["loop"] + ["slp%d" % n for n in r["slp"]] + (["mix"] if r["mix"] else []):
                est.append([cpu, test, cyc.get(("upstream", test), ""), cyc[("patched", test)]])

    say("%s %s on IBM Z (opt -O2 + llc, cycles from llvm-mca)\n" % (ty, op))
    say("1. Reported cost vs. instructions emitted (standalone%s)\n"
        % (", calling-convention vl/vst left out" if width == 128 else ""))
    say("  %-4s %14s %13s %7s %11s" % ("cpu", "upstream cost", "patched cost", "instr", "mca cycles"))
    for cpu in cpus:
        r = rows[cpu]
        say("  %-4s %14s %13d %7d %11.2f" % (cpu, r["up"] if r["up"] is not None else "n/a", r["pa"], r["n"], r["c_intr"]))

    base = rows[cpus[0]]["c_loop"]
    say("\n2. Loop d[i] = %s(s[i]): what each generation's hardware buys\n" % op)
    say("  %-4s %15s %15s %13s %9s" % ("cpu", "elements/trip", "instr/element", "cycles/elem", "vs " + cpus[0]))
    for cpu in cpus:
        r = rows[cpu]
        say("  %-4s %15s %15.2f %13.2f %8.1fx" % (
            cpu, "%d%s" % (r["elems"], " vectorized" if r["vec"] else ""), r["work"], r["c_loop"], base / r["c_loop"]))
    say("  An element is one %s value. instr/element counts the %s work only:" % (ty, op))
    say("  loads, stores and loop control are left out.")

    say("\n3. Patched cost vs. instructions per element inside the loop\n")
    say("  %-4s %13s %15s  %s" % ("cpu", "patched cost", "instr/element", ""))
    for cpu in cpus:
        r = rows[cpu]
        if abs(r["pa"] - r["work"]) < 0.01:
            note = "matches"
        elif r["work"] < r["pa"]:
            note = "loop needs fewer (vectorized)" if r["vec"] else "loop needs fewer (constants created once per loop)"
        else:
            note = "loop needs more"
        say("  %-4s %13d %15.2f  %s" % (cpu, r["pa"], r["work"], note))

    if any("c_split_patched" in rows[c] for c in cpus):
        alt = "one scalar %s per lane" % op if is_vec else "two half-width %s" % op
        say("\n4. Alternative: %s (@split), mca cycles standalone\n" % alt)
        say("  %-4s %10s %11s %11s %10s  %s" % ("cpu", "intrinsic", "split -O2", "split -O2", "split", ""))
        say("  %-4s %10s %11s %11s %10s" % ("", "", "upstream", "patched", "llc only"))
        for cpu in cpus:
            r = rows[cpu]
            ci, sp, su, sl = r["c_intr"], r["c_split_patched"], r.get("c_split_upstream"), r.get("c_split_llc")
            notes = []
            if su is not None and abs(su - sp) >= 0.01:
                notes.append("patch makes the -O2 split %.1fx %s" % (max(su, sp) / min(su, sp), "faster" if sp < su else "slower"))
            if abs(ci - sp) < 0.01 and sl is not None and abs(sl - ci) >= 0.01:
                notes.append("-O2 turns the split back into the intrinsic")
            if sl is not None and sl < ci - 0.01:
                notes.append(("scalarizing" if is_vec else "a split lowering") + " would be %.1fx FASTER" % (ci / sl))
            say("  %-4s %10.2f %11s %11.2f %10s  %s" % (cpu, ci, "%.2f" % su if su is not None else "n/a", sp,
                                                       "%.2f" % sl if sl is not None else "n/a", "; ".join(notes)))

    def ratio(u, p):
        if abs(u - p) < 0.01:
            return "same"
        return "patched %.1fx %s" % (max(u, p) / min(u, p), "FASTER" if p < u else "SLOWER")

    if have_up and any(rows[c]["slp"] for c in cpus):
        say("\n5. N values loaded from memory (@slpN): instructions / mca cycles, upstream -> patched\n")
        sizes = sorted(rows[cpus[0]]["slp"])
        say("  %-4s " % "cpu" + "".join("%-36s" % ("slp%d" % n) for n in sizes))
        for cpu in cpus:
            cells = []
            for n in sizes:
                d = rows[cpu]["slp"][n]
                (iu, cu, vu), (ip, cp, vp) = d["upstream"], d["patched"]
                tag = lambda v: "v" if v else "s"
                short = "" if abs(cu - cp) < 0.01 else "(%.1fx %s)" % (max(cu, cp) / min(cu, cp),
                                                                     "faster" if cp < cu else "SLOWER")
                cells.append("%-36s" % ("%d%s/%.1f -> %d%s/%.1f %s" % (iu, tag(vu), cu, ip, tag(vp), cp, short)))
            say("  %-4s " % cpu + "".join(cells))
        say("  s = scalar code, v = uses vector instructions.")

    if have_up and any(rows[c]["mix"] for c in cpus):
        say("\n6. Mixed-width loop (@mix): elements per trip and cycles per element\n")
        say("  %-4s %22s %22s  %s" % ("cpu", "upstream", "patched", ""))
        for cpu in cpus:
            m = rows[cpu]["mix"]
            (eu, cu), (ep, cp) = m["upstream"], m["patched"]
            say("  %-4s %22s %22s  %s" % (cpu, "%d elem, %.3f c/e" % (eu, cu), "%d elem, %.3f c/e" % (ep, cp),
                                         ratio(cu, cp)))

    if have_up:
        fns = rows[cpus[0]]["fns"]
        say("\n7. Does the patched opt change the generated code? (per function)\n")
        say("  %-4s " % "cpu" + "".join("%10s" % f for f in fns))
        for cpu in cpus:
            sm = rows[cpu]["same"]
            say("  %-4s " % cpu + "".join("%10s" % ("same" if sm[f] else "CHANGED") for f in fns))
    else:
        say("\n5-7. Skipped: %s not found, so upstream cannot be compared." % OPT_UP)

    with open(os.path.join(out, "results.txt"), "w") as f:
        f.write("\n".join(report) + "\n")
    print("\nSaved to %s" % os.path.relpath(os.path.join(out, "results.txt")))

    if not a.no_prebuilt:
        pb = os.path.join(folder, "prebuilt")
        with open(os.path.join(pb, "MANIFEST.txt"), "w") as f:
            f.write("# Written by bitcount_toy.py; read by toy_on_z.sh.\n")
            f.write("built: %s\nllvm_commit: %s\npatch_fingerprint: %s\ncpus: %s\n"
                    % (datetime.datetime.now().isoformat(timespec="seconds"),
                       llvm_git("rev-parse", "--short", "HEAD") or "unknown", patch_fingerprint(), " ".join(cpus)))
        with open(os.path.join(pb, "estimates.csv"), "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["cpu", "test", "upstream_cycles", "patched_cycles"])
            wr.writerows([[c, t, "%.3f" % u if u != "" else "", "%.3f" % p] for c, t, u, p in est])
        print("Refreshed %s (objects for toy_on_z.sh on IBM Z hardware)" % os.path.relpath(pb))


if __name__ == "__main__":
    main()
