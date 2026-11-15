// Everyday C++ that hits the three patterns where the SystemZ bit-count cost
// patch speeds up the generated code (see ../README.md). run_cpp.py compiles
// this file for s390x and measures each extern "C" function with the upstream
// and the patched LLVM.
//
// Freestanding on purpose: no standard headers are needed, so the file builds
// with any clang without an s390x sysroot.
//
// The input and output pointers are __restrict: without it the compiler must
// assume out[0] may overlap in[1], so it cannot load in[1] before storing
// out[0], and the two operations cannot be combined (with or without the patch).

typedef unsigned int u32;
typedef unsigned long long u64;
typedef unsigned __int128 u128;

// Same meaning as C++20 std::countr_zero / std::popcount (defined for 0).
static inline u32 countr_zero32(u32 x) { return x ? __builtin_ctz(x) : 32; }
static inline u32 popcount32(u32 x) { return __builtin_popcount(x); }

// There is no portable 128-bit popcount builtin, so code usually adds the
// counts of the two 64-bit halves.
static inline u64 popcount128(u128 x) {
  return __builtin_popcountll((u64)x) + __builtin_popcountll((u64)(x >> 64));
}

// 1. Slot allocator: index of the first free slot in each of two 32-slot
//    bitmaps (bit set = slot free; 32 when a bitmap is full).
//    Pattern: two i32 cttz on values loaded from memory.
extern "C" void first_free_slots(const u32 *__restrict free_bitmaps,
                                 u32 *__restrict first_free) {
  first_free[0] = countr_zero32(free_bitmaps[0]);
  first_free[1] = countr_zero32(free_bitmaps[1]);
}

// 2. Permission check: how many permission bits are set in a user's and a
//    group's 32-bit flag word.
//    Pattern: two i32 ctpop on values loaded from memory.
extern "C" void count_permissions(const u32 *__restrict flag_words,
                                  u32 *__restrict counts) {
  counts[0] = popcount32(flag_words[0]);
  counts[1] = popcount32(flag_words[1]);
}

// 3. 128-bit bitset (e.g. a small fixed-size set of 128 IDs): number of
//    members, computed from the two 64-bit halves.
//    Pattern: ctpop of the two halves of an i128.
extern "C" u64 bitset128_size(const u128 *set) { return popcount128(*set); }

// 4. The same count for a 128-bit value passed by value (e.g. a helper that
//    is not inlined). On IBM Z an unsigned __int128 argument arrives in
//    memory and is loaded into a vector register, unlike example 3, where the
//    two halves can be loaded straight into general registers.
//    Pattern: ctpop of the two halves of an i128 that is in a vector register.
extern "C" u128 popcount_u128(u128 x) { return popcount128(x); }
