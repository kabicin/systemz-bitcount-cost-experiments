/* Benchmark driver for the functions in examples.cpp, run on IBM Z hardware.
 *
 * run_on_z.sh links this file with one prebuilt object (upstream or patched
 * LLVM, one -march level) from prebuilt/. It
 *   1. checks every function against portable reference code on many inputs
 *      (exit 2 on any mismatch), then
 *   2. times each function: the median over REPEATS runs of the average
 *      nanoseconds per call over a buffer of independent inputs.
 * Output lines: "<function> <median ns per call> <min ns per call>".
 *
 * The functions live in a separate object file, so they cannot be inlined
 * into the timing loops; each call is a real call, as in the C++ source.
 */
#define _POSIX_C_SOURCE 199309L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef uint32_t u32;
typedef uint64_t u64;
typedef unsigned __int128 u128;

/* Defined in the prebuilt object (examples.cpp). */
void first_free_slots(const u32 *restrict free_bitmaps, u32 *restrict first_free);
void count_permissions(const u32 *restrict flag_words, u32 *restrict counts);
u64 bitset128_size(const u128 *set);
u128 popcount_u128(u128 x);

#define N 4096       /* independent inputs per timing pass */
#define PASSES 2000  /* passes over the inputs per timed run */
#define REPEATS 9    /* timed runs; the median is reported */

static u32 in32[2 * N], out32[2 * N];
static u128 in128[N];
static volatile u64 sink;

/* Reference implementations: plain loops, no builtins. */
static u32 ref_ctz32(u32 x) {
  u32 n = 0;
  if (!x) return 32;
  while (!(x & 1)) { x >>= 1; n++; }
  return n;
}
static u32 ref_pop32(u32 x) { u32 n = 0; while (x) { n += x & 1; x >>= 1; } return n; }
static u64 ref_pop128(u128 x) { u64 n = 0; while (x) { n += (u64)(x & 1); x >>= 1; } return n; }

static u64 rng = 0x9E3779B97F4A7C15ull; /* fixed seed: same inputs on every run */
static u64 next64(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }

static void make_inputs(void) {
  for (int i = 0; i < 2 * N; i++) {
    u32 v = (u32)next64();
    if (i % 17 == 0) v = 0;                      /* full bitmap / no flags */
    else if (i % 13 == 0) v = 0xFFFFFFFFu;
    else if (i % 11 == 0) v = 1u << (next64() % 32);
    in32[i] = v;
  }
  for (int i = 0; i < N; i++) {
    u128 v = ((u128)next64() << 64) | next64();
    if (i % 17 == 0) v = 0;
    else if (i % 13 == 0) v = ~(u128)0;
    else if (i % 11 == 0) v = (u128)1 << (next64() % 128);
    in128[i] = v;
  }
}

static int check(void) {
  int bad = 0;
  for (int i = 0; i < N; i++) {
    first_free_slots(&in32[2 * i], &out32[2 * i]);
    for (int k = 0; k < 2; k++)
      if (out32[2 * i + k] != ref_ctz32(in32[2 * i + k]) && bad++ < 5)
        fprintf(stderr, "first_free_slots(%#x) = %u, expected %u\n",
                in32[2 * i + k], out32[2 * i + k], ref_ctz32(in32[2 * i + k]));
    count_permissions(&in32[2 * i], &out32[2 * i]);
    for (int k = 0; k < 2; k++)
      if (out32[2 * i + k] != ref_pop32(in32[2 * i + k]) && bad++ < 5)
        fprintf(stderr, "count_permissions(%#x) = %u, expected %u\n",
                in32[2 * i + k], out32[2 * i + k], ref_pop32(in32[2 * i + k]));
    if (bitset128_size(&in128[i]) != ref_pop128(in128[i]) && bad++ < 5)
      fprintf(stderr, "bitset128_size wrong for input %d\n", i);
    if (popcount_u128(in128[i]) != (u128)ref_pop128(in128[i]) && bad++ < 5)
      fprintf(stderr, "popcount_u128 wrong for input %d\n", i);
  }
  return bad;
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

/* One timed run of a function: average ns per call. */
static double run_once(int which) {
  u64 acc = 0;
  double t0 = now_ns();
  for (int p = 0; p < PASSES; p++) {
    switch (which) {
    case 0:
      for (int i = 0; i < N; i++) first_free_slots(&in32[2 * i], &out32[2 * i]);
      acc += out32[p % (2 * N)];
      break;
    case 1:
      for (int i = 0; i < N; i++) count_permissions(&in32[2 * i], &out32[2 * i]);
      acc += out32[p % (2 * N)];
      break;
    case 2:
      for (int i = 0; i < N; i++) acc += bitset128_size(&in128[i]);
      break;
    case 3:
      for (int i = 0; i < N; i++) acc += (u64)popcount_u128(in128[i]);
      break;
    }
  }
  double t1 = now_ns();
  sink += acc;
  return (t1 - t0) / ((double)PASSES * N);
}

int main(void) {
  static const char *names[] = {"first_free_slots", "count_permissions",
                                "bitset128_size", "popcount_u128"};
  make_inputs();
  int bad = check();
  if (bad) {
    fprintf(stderr, "FAIL: %d wrong results\n", bad);
    return 2;
  }
  for (int f = 0; f < 4; f++) {
    double t[REPEATS];
    run_once(f); /* warm up caches and branch predictors */
    for (int r = 0; r < REPEATS; r++) t[r] = run_once(f);
    qsort(t, REPEATS, sizeof t[0], cmp_double);
    printf("%s %.3f %.3f\n", names[f], t[REPEATS / 2], t[0]);
  }
  return 0;
}
