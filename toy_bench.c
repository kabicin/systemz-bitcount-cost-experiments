/* Benchmark driver for one toy folder (kernels.ll), run on IBM Z hardware.
 *
 * toy_on_z.sh links this file with one prebuilt object (upstream or patched
 * LLVM, one -march level) from <folder>/prebuilt/. The folder's type and
 * intrinsic are compile-time parameters, set from prebuilt/MANIFEST.txt:
 *
 *   -DW=<lane width in bits: 8, 16, 32, 64 or 128>
 *   -DLANES=<lanes per element: 1 for scalars, N for <N x iW>>
 *   -DOP=<0 ctlz | 1 cttz | 2 ctpop>
 *   -DHAVE_SLP2 -DHAVE_SLP4 -DHAVE_SLP8 -DHAVE_MIX    (the kernels that exist)
 *
 * It
 *   1. checks every kernel against portable reference code on many inputs
 *      (exit 2 on any mismatch), then
 *   2. times each kernel: the median over REPEATS runs.
 * Output lines: "<test> <median ns> <min ns>", where the unit is
 *   loop, mix   ns per element (one value of the type, or one vector)
 *   slp2/4/8    ns per call (a group of 2, 4 or 8 values)
 *
 * Only @loop, @slpN and @mix are used: they take pointers, so the same code
 * works for i128 and vector types, whose by-value passing differs by type.
 * The kernels live in a separate object file, so each call is a real call.
 */
#define _POSIX_C_SOURCE 199309L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#if !defined(W) || !defined(LANES) || !defined(OP)
#error "define W, LANES and OP (see the comment at the top of this file)"
#endif

typedef uint64_t u64;
typedef unsigned __int128 u128;

#define EB (LANES * W / 8)    /* bytes per element: one scalar or one vector */
#define OW (W < 64 ? 64 : 32) /* width of the @mix input e[] and output d[] */

#define N 4096       /* elements per pass */
#define PASSES 300   /* passes per timed @loop / @mix run */
#define SLP_PASSES (4 * PASSES)
#define REPEATS 7    /* timed runs; the median is reported */

void loop(void *restrict d, const void *restrict s, int64_t n);
#ifdef HAVE_SLP2
void slp2(void *restrict d, const void *restrict a);
#endif
#ifdef HAVE_SLP4
void slp4(void *restrict d, const void *restrict a);
#endif
#ifdef HAVE_SLP8
void slp8(void *restrict d, const void *restrict a);
#endif
#ifdef HAVE_MIX
void mix(void *restrict d, const void *restrict s, const void *restrict e, int64_t n);
#endif

static unsigned char src[N * EB] __attribute__((aligned(16)));
static unsigned char dst[N * EB] __attribute__((aligned(16)));
#ifdef HAVE_MIX
static unsigned char ext[N * OW / 8] __attribute__((aligned(16)));
static unsigned char mdst[N * OW / 8] __attribute__((aligned(16)));
#endif
static volatile u64 sink;

/* Lane j of an array: lanes are W bits wide and contiguous, in native byte order. */
static u128 get(const unsigned char *p, int j) {
  p += (size_t)j * (W / 8);
  switch (W) {
  case 8: return p[0];
  case 16: { uint16_t v; memcpy(&v, p, 2); return v; }
  case 32: { uint32_t v; memcpy(&v, p, 4); return v; }
  case 64: { u64 v; memcpy(&v, p, 8); return v; }
  default: { u128 v; memcpy(&v, p, 16); return v; }
  }
}

static void put(unsigned char *p, int j, u128 x) {
  p += (size_t)j * (W / 8);
  switch (W) {
  case 8: p[0] = (unsigned char)x; break;
  case 16: { uint16_t v = (uint16_t)x; memcpy(p, &v, 2); break; }
  case 32: { uint32_t v = (uint32_t)x; memcpy(p, &v, 4); break; }
  case 64: { u64 v = (u64)x; memcpy(p, &v, 8); break; }
  default: memcpy(p, &x, 16); break;
  }
}

/* Reference implementations: plain loops, no builtins. ctlz and cttz are
 * fully defined at 0: the kernels call them with the "zero is poison" flag false. */
static unsigned ref(u128 x) {
  unsigned n = 0;
  if (OP == 2) {
    while (x) { n += (unsigned)(x & 1); x >>= 1; }
    return n;
  }
  if (x == 0) return W;
  if (OP == 1) {
    while (!(x & 1)) { x >>= 1; n++; }
    return n;
  }
  for (int b = W - 1; b >= 0 && !((x >> b) & 1); b--) n++;
  return n;
}

static u128 mask(u128 v) { return W == 128 ? v : v & (((u128)1 << W) - 1); }

static u64 rng = 0x9E3779B97F4A7C15ull; /* fixed seed: same inputs on every run */
static u64 next64(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }

/* A random value, often with many leading or trailing zeros, plus the edge cases. */
static u128 rand_lane(int i) {
  u128 v = mask(((u128)next64() << 64) | next64());
  unsigned sh = (unsigned)(next64() % W);
  if (i % 5 == 0) v >>= sh;
  else if (i % 5 == 1) v = mask(v << sh);
  if (i % 17 == 0) v = 0;
  else if (i % 13 == 0) v = mask(~(u128)0);
  else if (i % 11 == 0) v = (u128)1 << sh;
  return v;
}

static void make_inputs(void) {
  for (int j = 0; j < N * LANES; j++) put(src, j, rand_lane(j));
#ifdef HAVE_MIX
  for (int i = 0; i < N; i++) {
    u64 y = next64();
    if (OW == 32) { uint32_t v = (uint32_t)y; memcpy(ext + 4 * i, &v, 4); }
    else memcpy(ext + 8 * i, &y, 8);
  }
#endif
}

static int bad;

static void expect(const char *what, int j, u128 got, u128 want) {
  if (got != want && bad++ < 5)
    fprintf(stderr, "%s: lane %d: input %016llx%016llx -> %016llx%016llx, expected %016llx%016llx\n", what, j,
            (unsigned long long)(get(src, j) >> 64), (unsigned long long)get(src, j),
            (unsigned long long)(got >> 64), (unsigned long long)got,
            (unsigned long long)(want >> 64), (unsigned long long)want);
}

static void check_dst(const char *what) {
  for (int j = 0; j < N * LANES; j++) expect(what, j, get(dst, j), ref(get(src, j)));
}

static double now_ns(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1e9 + t.tv_nsec;
}

static int cmp_double(const void *a, const void *b) {
  double x = *(const double *)a, y = *(const double *)b;
  return (x > y) - (x < y);
}

/* d[i] = op(s[i]) over N elements. Time: ns per element. */
static void check_loop(void) { memset(dst, 0, sizeof dst); loop(dst, src, N); check_dst("loop"); }
static double time_loop(void) {
  double t0 = now_ns();
  for (int p = 0; p < PASSES; p++) loop(dst, src, N);
  double t1 = now_ns();
  sink += dst[PASSES % (N * EB)];
  return (t1 - t0) / ((double)PASSES * N);
}

/* N / K calls of slpK, each bit-counting K values. Time: ns per call. */
#define SLP(K)                                                                   \
  static void check_slp##K(void) {                                               \
    memset(dst, 0, sizeof dst);                                                  \
    for (int g = 0; g < N / K; g++) slp##K(dst + (size_t)g * K * EB, src + (size_t)g * K * EB); \
    check_dst("slp" #K);                                                         \
  }                                                                              \
  static double time_slp##K(void) {                                              \
    double t0 = now_ns();                                                        \
    for (int p = 0; p < SLP_PASSES; p++)                                         \
      for (int g = 0; g < N / K; g++) slp##K(dst + (size_t)g * K * EB, src + (size_t)g * K * EB); \
    double t1 = now_ns();                                                        \
    sink += dst[SLP_PASSES % (N * EB)];                                          \
    return (t1 - t0) / ((double)SLP_PASSES * (N / K));                           \
  }
#ifdef HAVE_SLP2
SLP(2)
#endif
#ifdef HAVE_SLP4
SLP(4)
#endif
#ifdef HAVE_SLP8
SLP(8)
#endif

/* d[i] = ext(op(s[i])) + e[i] with d and e of OW bits. Time: ns per element. */
#ifdef HAVE_MIX
static void check_mix(void) {
  memset(mdst, 0, sizeof mdst);
  mix(mdst, src, ext, N);
  for (int i = 0; i < N; i++) {
    u64 y, got;
    if (OW == 32) { uint32_t a, b; memcpy(&a, ext + 4 * i, 4); memcpy(&b, mdst + 4 * i, 4); y = a; got = b; }
    else { memcpy(&y, ext + 8 * i, 8); memcpy(&got, mdst + 8 * i, 8); }
    u64 want = (u64)ref(get(src, i)) + y;
    if (OW == 32) want &= 0xFFFFFFFFull;
    expect("mix", i, got, want);
  }
}
static double time_mix(void) {
  double t0 = now_ns();
  for (int p = 0; p < PASSES; p++) mix(mdst, src, ext, N);
  double t1 = now_ns();
  sink += mdst[PASSES % (N * OW / 8)];
  return (t1 - t0) / ((double)PASSES * N);
}
#endif

struct test {
  const char *name;
  void (*check)(void);
  double (*time)(void);
};

static const struct test tests[] = {
  {"loop", check_loop, time_loop},
#ifdef HAVE_SLP2
  {"slp2", check_slp2, time_slp2},
#endif
#ifdef HAVE_SLP4
  {"slp4", check_slp4, time_slp4},
#endif
#ifdef HAVE_SLP8
  {"slp8", check_slp8, time_slp8},
#endif
#ifdef HAVE_MIX
  {"mix", check_mix, time_mix},
#endif
};

int main(void) {
  size_t nt = sizeof tests / sizeof tests[0];
  make_inputs();
  for (size_t t = 0; t < nt; t++) tests[t].check();
  if (bad) {
    fprintf(stderr, "FAIL: %d wrong results\n", bad);
    return 2;
  }
  for (size_t t = 0; t < nt; t++) {
    double r[REPEATS];
    tests[t].time(); /* warm up caches and branch predictors */
    for (int i = 0; i < REPEATS; i++) r[i] = tests[t].time();
    qsort(r, REPEATS, sizeof r[0], cmp_double);
    printf("%s %.3f %.3f\n", tests[t].name, r[REPEATS / 2], r[0]);
  }
  return 0;
}
