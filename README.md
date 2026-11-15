# SystemZ bit-count cost toys (IBM Z)

Runnable experiments for the SystemZ bit-count cost patch (branch
`systemz-bitcount-costs` in `../llvm-project`). Each one compiles small
kernels with the upstream and patched `opt`, then `llc`, for z13–z17, and
times the code with `llvm-mca`.

| What | Command | Shows |
|---|---|---|
| One folder per type and intrinsic (`i128-ctlz`, `v8i32-cttz`, ...) | `./run_all.sh` | Reported cost vs. real code, what each generation's hardware buys, and whether the patch changes the generated code |

You need `opt`, `llc` and `llvm-mca` in `../llvm-project/build/bin`, plus
`opt-upstream` (an `opt` built without the patch; see the last section).
Set `LLVM_BIN` to use a different directory.

## Per-type toys

```sh
./run_all.sh                         # every folder
./i128-ctlz/run.sh                   # one folder
./i128-ctlz/run.sh --cpus z13,z17    # selected CPUs
./new_toy.py i32 ctpop               # add a folder: scalar i8..i128
./new_toy.py v8i32 ctlz cttz         # or a vector type vNiW
```

- `new_toy.py` writes `<type>-<op>/kernels.ll` and `<type>-<op>/run.sh`.
- `bitcount_toy.py` measures one folder; the folder name gives the type and
  intrinsic. Results are printed and saved to `<folder>/out/results.txt`,
  with every assembly file and timed path next to them.

Each `kernels.ll` has three functions:

| Function | What it is |
|---|---|
| `@intrinsic` | The intrinsic on its own |
| `@split` | The same result another way: two half-width intrinsics (scalars, i16 and wider) or one scalar intrinsic per lane (vectors) |
| `@loop` | `d[i] = op(s[i])` over an array |
| `@slp2`, `@slp4`, `@slp8` | 2, 4 or 8 values loaded from memory, bit-counted, stored back (scalar types; up to 256 bits in total). The SLP vectorizer's shape |
| `@mix` | `d[i] = ext(op(s[i])) + e[i]` mixing two widths, so the loop vectorizer has to choose a vectorization factor (scalar types below i128) |

The script prints seven sections: reported cost vs. instructions emitted,
loop cycles per element by generation, patched cost vs. per-element work in
the loop, the `@split` alternative (at `-O2` upstream and patched, and
compiled by `llc` alone), the `@slpN` groups, the `@mix` loop, and which
functions the patch changes.

### All folders at a glance

Cost is the throughput cost `opt` reports, upstream → patched, for
z13 / z14 / z15 / z16 / z17. With the patch every standalone cost equals
the instructions emitted.

| Folder | Upstream cost | Patched cost | Does the patch change -O2 code? |
|---|---|---|---|
| `i32-cttz` | 1 | 9 / 9 / 4 / 4 / 2 | Leftover-elements loop only (z13/z14); main loop identical |
| `i64-cttz` | 1 | 6 / 6 / 3 / 3 / 1 | No |
| `i32-ctpop` | 2 / 2 / 1 / 1 / 1 | 6 / 6 / 2 / 2 / 2 | **`@split` 3.2× slower (z13), 2.4× (z14)**; leftover loop on z14 |
| `i64-ctpop` | 2 / 2 / 1 / 1 / 1 | 8 / 8 / 1 / 1 / 1 | **`@split` 1.9× slower (z13), 1.5× (z14)** |
| `i128-ctpop` | 2 | 5 / 3 / 3 / 3 / 3 | **`@split` 2.7× faster (z13), 4.4× (z14)** |
| `i128-ctlz` | 1 | 26 / 24 / 24 / 24 / 1 | No (a split *lowering* would be 1.6× faster on z13) |
| `i128-cttz` | 1 | 8 / 6 / 6 / 6 / 1 | No |
| `v8i16-ctpop` | 2 / 1 / 1 / 1 / 1 | 4 / 1 / 1 / 1 / 1 | No |
| `v4i32-ctpop` | 2 / 1 / 1 / 1 / 1 | 3 / 1 / 1 / 1 / 1 | No |
| `v8i32-ctlz` | 4 | 2 | No |
| `v8i32-cttz` | 4 | 2 | No |

**Why the `@split` popcount results go both ways.** In each case SLP now
combines the two half-width popcounts into one vector popcount, because
the scalar popcount costs more than before.

- For `i128` the value is already in a vector register, so this wins.
- For `i32`/`i64` the value is in a general register. SLP has to move it
  into a vector register, shift the lanes and move the result back. SystemZ
  prices those steps too low (the per-lane shift costs 0 but needs `vgmg` +
  `vesrlvf`), so SLP's total is −1 while the code really gets longer and
  slower.

The more common pattern `ctpop(a) + ctpop(b)` (and `cttz`) on separate
i32/i64 values is **not** affected: the code is identical with and without
the patch.

### Values loaded from memory (`@slpN`) and mixed-width loops (`@mix`)

| Folder | `@slp2` (llvm-mca cycles, upstream → patched) | `@slp4`, `@slp8` | `@mix` |
|---|---|---|---|
| `i32-cttz` | **z13/z14 14.0 → 1.5 (9.2× faster); z15/z16 5.5 → 1.5 (3.6×); z17 3.5 → 1.5 (2.3×)** | same | same |
| `i32-ctpop` | **z13 14.0 → 1.9 (7.3× faster); z15–z17 2.0 → 1.5 (1.3×)**; z14 already vector | same | same |
| `i64-cttz`, `i64-ctpop` | same (already vector upstream) | same | same |
| `i128-*` | same | n/a | n/a |

Two i32 values loaded from memory are where the patch helps most. Upstream
priced each scalar cttz at 1, so SLP's total was +1 and it kept 22 scalar
instructions on z13. The patch prices them at 9, SLP's total is −15, and it
emits `vlrepg` + one `vctzf` + two `vstef`. Groups of 4 and 8, and i64 pairs,
were already vectorized upstream. No `@mix` loop changed: the bit count is
too small a part of the loop to move the vectorization factor.

### What the i128 folders show (z13 numbers unless stated)

| | ctlz | cttz | ctpop |
|---|---|---|---|
| Patched cost (upstream) | 26 (1) | 8 (1) | 5 (2) |
| Loop cycles/element, z13 → z17 | 13.77 → 1.00 (13.7×) | 3.39 → 1.00 (3.4×) | 2.27 → 1.01 (2.2×, from z14) |
| Instructions per element in a loop | 18 | 6 | 4 |
| Split lowering (llc only) vs. intrinsic | **1.6× faster** (1.2× on z14–z16) | 2.5× slower | 4.7× slower |
| Generated code changed by the patch | no | no | **yes: `@split` 2.7× faster (4.4× on z14)** |

- **ctlz: a possible backend improvement.** Computing i128 ctlz as
  `hi != 0 ? ctlz(hi) : 64 + ctlz(lo)` with two `flogr` takes 10.03 cycles
  vs. 16.28 for today's vector sequence on z13 (12.08 on z14–z16). At `-O2`
  InstCombine folds a hand-written split back into `llvm.ctlz.i128`, so
  this would have to be a lowering change in `SystemZISelLowering.cpp`,
  used when VE3 (z17 `vclzq`) is not available. The split was checked
  against the intrinsic on 10 edge-case values.
- **ctpop: a speedup caused by the patch.** Two 64-bit popcounts used to
  cost 2 each, so SLP left them as two 8-instruction scalar sequences. The
  patch says 8 each, SLP finds the vector form cheaper (cost −12) and emits
  one `vpopct` sequence: 12.03 → 4.42 cycles on z13, 12.03 → 2.71 on z14.
  The same SLP decision should apply to other pairs of independent i64
  popcounts, but only this case has been measured.
- **All three:** inside a loop the zero/all-ones constants are created once,
  so the real per-element count is lower than the patched cost (for
  example 18 vs. 26 for ctlz).

## Why the patch has fix A

Fix A gives the measured scalar and i128 costs only to throughput queries
(the vectorizers). Before it, SimplifyCFG (which asks for size+latency) saw
the higher scalar cttz cost and kept branches it used to turn into
selects. On z13, measured with llvm-mca, that made:

- `if (flag[i]) state = cttz(state ^ x[i]) + 1;` up to 8.7× faster
  (a loop-carried chain no longer runs cttz on every element), and
- `sum += flag[i] ? cttz(x[i]) + 1 : 0;` up to 2.25× slower (InstCombine
  sank the load of `x[i]` into the kept branch, and the loop vectorizer gave
  up because IBM Z has no masked loads).

With fix A both loops compile exactly as upstream. The runnable version of
this experiment has been removed.

## Building `opt-upstream`

From `../llvm-project`, with the patch applied:

```sh
F=llvm/lib/Target/SystemZ/SystemZTargetTransformInfo.cpp
cp $F /tmp/patched.cpp
git checkout -- $F
make -C build -j8 opt && cp build/bin/opt build/bin/opt-upstream
cp /tmp/patched.cpp $F
make -C build -j8 opt
```

Only that one file changes, so each rebuild takes well under a minute.
