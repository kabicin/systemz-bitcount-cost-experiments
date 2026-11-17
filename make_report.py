#!/usr/bin/env python3
"""Build bitcount-improvements.xlsx from the experiment results.

  python3 make_report.py                            local out/results.txt files and records/hw-history.csv
  python3 make_report.py export-z/z16-....tar.gz    add the hardware rows of an export from run_on_z.sh
  python3 make_report.py hw-history.csv more.csv    one or more CSV files, folders or .tar.gz files
  python3 make_report.py -o report.xlsx ...         choose the output file

Sheets: Summary, Cost Model, Toy Timings and C++ Timings (llvm-mca estimates, read
from <folder>/out/results.txt and cpp/out/results.txt when they exist next to this
script) and "Hardware (IBM Z)" (measured ns from run_on_z.sh). A folder that has
only the hardware CSV, such as an export bundle, gives just the hardware sheets.

Needs Python 3 and the openpyxl package (pip install openpyxl). Without openpyxl it
writes hardware.csv instead, which opens in any spreadsheet.
"""
import csv
import glob
import io
import os
import re
import sys
import tarfile

ROOT = os.path.dirname(os.path.abspath(__file__))
CPUS = ["z13", "z14", "z15", "z16", "z17"]

# Costs from the README table, by (type, op) for z13..z17. ctlz/cttz use the "without
# zero poison" figure because the toys call the intrinsic with i1 false. Update this
# when the README table changes.
README = {
    ("i128", "ctlz"): [26, 24, 24, 24, 1], ("i128", "cttz"): [8, 6, 6, 6, 1],
    ("i64", "cttz"): [6, 6, 3, 3, 1], ("i32", "cttz"): [9, 9, 4, 4, 2],
    ("v8i32", "ctlz"): [2] * 5, ("v8i32", "cttz"): [2] * 5, ("i128", "ctpop"): [5, 3, 3, 3, 3],
}


# ---------------------------------------------------------------- reading results
def section(text, n):
    m = re.search(r"^%d\. .*?\n(.*?)(?=^\d\. |\Z)" % n, text, re.S | re.M)
    return m.group(1) if m else ""


def read_estimates():
    """llvm-mca results written by bitcount_toy.py and cpp/run_cpp.py (empty lists if absent)."""
    cost, toys, cpp = [], [], []
    for d in sorted(glob.glob(os.path.join(ROOT, "*-ct*/"))):
        path = os.path.join(d, "out", "results.txt")
        if not os.path.exists(path):
            continue
        ty, op = os.path.basename(d.rstrip("/")).rsplit("-", 1)
        t = open(path).read()
        for l in section(t, 1).splitlines():
            m = re.match(r"\s+(z\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)", l)
            if m:
                cost.append([ty, op, m[1], int(m[2]), int(m[3]), int(m[4]), float(m[5])])
        s5 = section(t, 5).splitlines()
        sizes = re.findall(r"slp(\d+)", s5[1] if len(s5) > 1 else "")
        for l in s5:
            m = re.match(r"\s+(z\d+)\s+(.*)", l)
            if m and "->" in l:
                for n, c in zip(sizes, re.findall(r"(\d+)[vs]/([\d.]+) -> (\d+)[vs]/([\d.]+)", m[2])):
                    toys.append([ty, op, m[1], "slp" + n, int(c[0]), float(c[1]), int(c[2]), float(c[3])])
        for l in section(t, 6).splitlines():
            m = re.match(r"\s+(z\d+)\s+(\d+) elem, ([\d.]+) c/e\s+(\d+) elem, ([\d.]+) c/e", l)
            if m:
                toys.append([ty, op, m[1], "mix (cycles/elem)", int(m[2]), float(m[3]), int(m[4]), float(m[5])])
    path = os.path.join(ROOT, "cpp", "out", "results.txt")
    if os.path.exists(path):
        for l in open(path).read().splitlines():
            m = re.match(r"\s+(\w+)\s+(z\d+)\s+(\d+) instr ([\d.]+)c\s+(\d+) instr ([\d.]+)c", l)
            if m:
                cpp.append([m[1], m[2], int(m[3]), float(m[4]), int(m[5]), float(m[6])])
    return cost, toys, cpp


def read_hw_text(text):
    return list(csv.DictReader(io.StringIO(text)))


def read_hw(source):
    """Rows of hw-history.csv from a CSV file, a folder (searched recursively) or a .tar.gz."""
    if os.path.isdir(source):
        files = sorted(glob.glob(os.path.join(source, "**", "hw-history.csv"), recursive=True))
        if not files:
            sys.exit("error: no hw-history.csv under %s" % source)
        return [r for f in files for r in read_hw_text(open(f, newline="").read())]
    if tarfile.is_tarfile(source):
        rows = []
        with tarfile.open(source) as tar:
            for member in tar.getmembers():
                if member.isfile() and os.path.basename(member.name) == "hw-history.csv":
                    rows += read_hw_text(tar.extractfile(member).read().decode())
        if not rows:
            sys.exit("error: no hw-history.csv rows in %s" % source)
        return rows
    if not os.path.exists(source):
        sys.exit("error: %s not found" % source)
    return read_hw_text(open(source, newline="").read())


def clean_hw(rows):
    """Keep the newest row per (machine, march, function); drop rows without usable timings."""
    latest = {}
    for r in rows:
        try:
            up, pa = float(r["upstream_ns"]), float(r["patched_ns"])
        except (KeyError, ValueError, TypeError):
            continue
        if up <= 0 or pa <= 0:
            continue
        key = (r["machine"], r["march"], r["function"])
        if key not in latest or r["timestamp"] >= latest[key]["timestamp"]:
            latest[key] = dict(r, upstream_ns=up, patched_ns=pa)
    return sorted(latest.values(), key=lambda r: (r["machine"], "/" not in r["function"],
                                                  r["function"], r["march"]))


def mca(r):
    try:
        return float(r["mca_speedup"])
    except (KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------- output
def write_csv_only(hw, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["machine_type", "machine", "kernel", "march", "function", "upstream_ns", "patched_ns",
                    "speedup", "mca_speedup", "timestamp"])
        for r in hw:
            w.writerow([r["machine_type"], r["machine"], r["kernel"], r["march"], r["function"],
                        r["upstream_ns"], r["patched_ns"], "%.3f" % (r["upstream_ns"] / r["patched_ns"]),
                        r["mca_speedup"], r["timestamp"]])
    print("openpyxl is not installed (pip install openpyxl); wrote %s with %d rows instead" % (path, len(hw)))


def build_workbook(cost, toys, cpp, hw, out):
    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    HDR = PatternFill("solid", fgColor="1F4E79")
    GOOD, BAD = PatternFill("solid", fgColor="C6EFCE"), PatternFill("solid", fgColor="FFC7CE")

    def sheet(ws, headers, rows, widths, fmts=None):
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), HDR
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        for col, f in (fmts or {}).items():
            for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
                row[0].number_format = f
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        ws.row_dimensions[1].height = 45

    def color(ws, col, last, good, bad):
        rng = "%s2:%s%d" % (col, col, last)
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"%s"' % good], fill=GOOD))
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"%s"' % bad], fill=BAD))

    first = [True]

    def new_sheet(title):
        if first[0]:
            first[0] = False
            ws = wb.active
            ws.title = title
            return ws
        return wb.create_sheet(title)

    ref = {}   # sheet name -> last data row

    if cost:
        ws = new_sheet("Cost Model")
        rows = []
        for i, (ty, op, cpu, up, pa, ins, cyc) in enumerate(cost, 2):
            rows.append([ty, op, cpu, up, pa, ins, cyc,
                         "=E%d-D%d" % (i, i), "=ABS(D%d-F%d)" % (i, i), "=ABS(E%d-F%d)" % (i, i),
                         '=IF(J%d<I%d,"More accurate",IF(J%d=I%d,"Same","Less accurate"))' % (i, i, i, i),
                         README[(ty, op)][CPUS.index(cpu)] if (ty, op) in README and cpu in CPUS else None,
                         '=IF(L%d="","n/a",IF(E%d=L%d,"Matches","DIFFERS"))' % (i, i, i)])
        sheet(ws, ["Type", "Op", "CPU", "Upstream cost", "Patched cost", "Instructions emitted",
                   "llvm-mca cycles (patched code)", "Cost change", "Upstream error vs instr", "Patched error vs instr",
                   "Accuracy", "README table cost", "Patched vs README table"], rows,
              [9, 8, 7, 11, 11, 13, 16, 10, 13, 13, 16, 12, 14], {7: "0.00"})
        ref["Cost Model"] = len(rows) + 1
        color(ws, "K", ref["Cost Model"], "More accurate", "Less accurate")
        color(ws, "M", ref["Cost Model"], "Matches", "DIFFERS")

    if toys:
        ws = new_sheet("Toy Timings")
        rows = []
        for i, (ty, op, cpu, test, iu, cu, ip, cp) in enumerate(toys, 2):
            rows.append([ty, op, cpu, test, iu, cu, ip, cp,
                         "=IF(H%d=0,0,F%d/H%d)" % (i, i, i),
                         '=IF(ABS(F%d-H%d)<0.01,"Same",IF(H%d<F%d,"Faster","SLOWER"))' % (i, i, i, i)])
        sheet(ws, ["Type", "Op", "CPU", "Test", "Upstream instr (elem/trip for mix)", "Upstream cycles",
                   "Patched instr (elem/trip for mix)", "Patched cycles", "Speedup (upstream / patched)", "Effect"],
              rows, [9, 8, 7, 18, 16, 12, 16, 12, 14, 10], {6: "0.00", 8: "0.00", 9: '0.00"x"'})
        ref["Toy Timings"] = len(rows) + 1
        color(ws, "J", ref["Toy Timings"], "Faster", "SLOWER")

    if cpp:
        ws = new_sheet("C++ Timings")
        rows = []
        for i, (fn, cpu, iu, cu, ip, cp) in enumerate(cpp, 2):
            rows.append([fn, cpu, iu, cu, ip, cp, "=D%d/F%d" % (i, i), "=C%d-E%d" % (i, i),
                         '=IF(ABS(D%d-F%d)<0.01,IF(C%d=E%d,"Same","Same speed, different code"),IF(F%d<D%d,"Faster","SLOWER"))'
                         % (i, i, i, i, i, i)])
        sheet(ws, ["Function", "CPU", "Upstream instr", "Upstream cycles", "Patched instr", "Patched cycles",
                   "Speedup (upstream / patched)", "Instructions saved", "Effect"],
              rows, [20, 7, 11, 11, 11, 11, 14, 11, 26], {4: "0.00", 6: "0.00", 7: '0.00"x"'})
        ref["C++ Timings"] = len(rows) + 1
        color(ws, "I", ref["C++ Timings"], "Faster", "SLOWER")

    if hw:
        ws = new_sheet("Hardware (IBM Z)")
        rows = []
        for i, r in enumerate(hw, 2):
            est = mca(r)
            rows.append([r["machine"], "toy" if "/" in r["function"] else "C++", r["function"], r["march"],
                         r["upstream_ns"], r["patched_ns"], "=E%d/F%d" % (i, i), est,
                         '=IF(H%d="","",G%d/H%d)' % (i, i, i),
                         '=IF(G%d>=1.05,"Faster",IF(G%d<=0.95,"SLOWER","Same (within 5%%)"))' % (i, i),
                         r["timestamp"], "%s %s" % (r["llvm_commit"], r["patch_fingerprint"])])
        sheet(ws, ["Machine", "Source", "Function / test", "-march", "Upstream ns", "Patched ns",
                   "Speedup (upstream / patched)", "llvm-mca estimate", "Measured / estimate", "Effect (5% band)",
                   "Run", "LLVM commit, patch"],
              rows, [9, 8, 28, 8, 12, 12, 14, 12, 12, 18, 20, 24],
              {5: "0.000", 6: "0.000", 7: '0.00"x"', 8: '0.00"x"', 9: "0.00"})
        ref["Hardware (IBM Z)"] = len(rows) + 1
        color(ws, "J", ref["Hardware (IBM Z)"], "Faster", "SLOWER")

    # ---- Summary
    s = wb.create_sheet("Summary", 0)
    s.append(["SystemZ bit-count cost patch: what improved"])
    s.append(["Cycles are llvm-mca estimates (no cache misses, perfect branch prediction)" +
              ("; ns on the Hardware sheet are measured." if hw else ", not hardware timings.")])
    s["A1"].font = Font(bold=True, size=14)
    s["A2"].font = Font(italic=True, color="57606A")
    pct, num, spd = [], [], []

    def head(title, col2="Count", col3="Share"):
        s.append([])
        s.append([title, col2, col3])
        for c in s[s.max_row][:3]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), HDR
        return s.max_row

    def line(label, formula, base=None, fmt=None):
        row = s.max_row + 1
        vals = [label, formula]
        if base:
            vals.append("=B%d/B%d" % (row, base))
            pct.append(row)
        s.append(vals)
        (spd if fmt == "spd" else num if fmt == "num" else []).append(row)
        return row

    def rng(sheet_name, col):
        return "'%s'!%s2:%s%d" % (sheet_name, col, col, ref[sheet_name])

    if cost:
        n = "Cost Model"
        head("Cost model accuracy (reported cost vs instructions emitted)")
        total = line("Type/op/CPU combinations", "=COUNTA(%s)" % rng(n, "A"))
        line("Cost changed by the patch", '=COUNTIF(%s,"<>0")' % rng(n, "H"), total)
        line("Patched cost closer to instructions emitted", '=COUNTIF(%s,"More accurate")' % rng(n, "K"), total)
        line("Patched cost further from instructions emitted", '=COUNTIF(%s,"Less accurate")' % rng(n, "K"), total)
        line("Average |cost - instructions|, upstream", "=AVERAGE(%s)" % rng(n, "I"), fmt="num")
        line("Average |cost - instructions|, patched", "=AVERAGE(%s)" % rng(n, "J"), fmt="num")
        have = line("Rows with a README table cost", "=COUNT(%s)" % rng(n, "L"))
        line("  patched cost matches the README table", '=COUNTIF(%s,"Matches")' % rng(n, "M"), have)
        line("  patched cost differs from the README table", '=COUNTIF(%s,"DIFFERS")' % rng(n, "M"), have)

    def effect_block(title, name, eff_col, spd_col, count_label):
        head(title)
        total = line(count_label, "=COUNTA(%s)" % rng(name, "A"))
        fast = line("Faster", '=COUNTIF(%s,"Faster")' % rng(name, eff_col), total)
        slow = line("Slower", '=COUNTIF(%s,"SLOWER")' % rng(name, eff_col), total)
        line("Unchanged (same, or same speed with different code)" if name == "C++ Timings" else "Same",
             "=B%d-B%d-B%d" % (total, fast, slow), total)
        line("Best speedup", "=MAX(%s)" % rng(name, spd_col), fmt="spd")
        line("Geometric mean speedup",
             "=EXP(SUMPRODUCT(LN(%s))/COUNT(%s))" % (rng(name, spd_col), rng(name, spd_col)), fmt="spd")
        return total

    if cpp:
        effect_block("C++ examples (llvm-mca cycles per call)", "C++ Timings", "I", "G", "Function/CPU pairs")
    if toys:
        head("Toy timings (SLP groups and mixed-width loop)")
        total = line("Measurements", "=COUNTA(%s)" % rng("Toy Timings", "A"))
        for lab, key in (("Faster", "Faster"), ("Slower", "SLOWER"), ("Same", "Same")):
            line(lab, '=COUNTIF(%s,"%s")' % (rng("Toy Timings", "J"), key), total)
    if hw:
        n = "Hardware (IBM Z)"
        machines = sorted({r["machine"] for r in hw})
        effect_block("Measured on IBM Z (%s)" % ", ".join(machines), n, "J", "G", "Measurements")
        for src in ("toy", "C++"):
            line("  geometric mean speedup, %s examples" % src,
                 '=IFERROR(EXP(SUMPRODUCT((%s="%s")*LN(%s))/COUNTIF(%s,"%s")),"n/a")'
                 % (rng(n, "B"), src, rng(n, "G"), rng(n, "B"), src), fmt="spd")
        head("Measured speedup by -march (average)", "Speedup", "")
        for cpu in sorted({r["march"] for r in hw}):
            line(cpu, '=AVERAGEIF(%s,"%s",%s)' % (rng(n, "D"), cpu, rng(n, "G")), fmt="spd")
    if cpp:
        head("C++ speedup by CPU (average of function speedups)", "Speedup", "")
        for cpu in sorted({r[1] for r in cpp}):
            line(cpu, '=AVERAGEIF(%s,"%s",%s)' % (rng("C++ Timings", "B"), cpu, rng("C++ Timings", "G")), fmt="spd")

    for r in pct:
        s["C%d" % r].number_format = "0%"
    for r in num:
        s["B%d" % r].number_format = "0.00"
    for r in spd:
        s["B%d" % r].number_format = '0.00"x"'
    s.column_dimensions["A"].width = 58
    s.column_dimensions["B"].width = 12
    s.column_dimensions["C"].width = 10
    wb.save(out)


def main():
    args, out = sys.argv[1:], os.path.join(ROOT, "bitcount-improvements.xlsx")
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return
    if "-o" in args:
        i = args.index("-o")
        out = args[i + 1]
        args = args[:i] + args[i + 2:]

    rows = []
    for a in args:
        rows += read_hw(a)
    if not args:
        local = os.path.join(ROOT, "records", "hw-history.csv")
        if os.path.exists(local):
            rows = read_hw(local)
    hw = clean_hw(rows)
    cost, toys, cpp = read_estimates()
    if not (cost or toys or cpp or hw):
        sys.exit("error: nothing to report: no out/results.txt files here and no hardware rows given")

    try:
        import openpyxl  # noqa: F401
    except ImportError:
        if not hw:
            sys.exit("error: openpyxl is not installed (pip install openpyxl)")
        write_csv_only(hw, os.path.join(os.path.dirname(os.path.abspath(out)), "hardware.csv"))
        return
    build_workbook(cost, toys, cpp, hw, out)
    print("wrote %s: %d cost rows, %d toy rows, %d C++ rows, %d hardware rows" %
          (out, len(cost), len(toys), len(cpp), len(hw)))


if __name__ == "__main__":
    main()
