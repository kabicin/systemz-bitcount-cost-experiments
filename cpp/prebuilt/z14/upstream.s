	.file	"examples.cpp"
	.text
	.globl	first_free_slots                # -- Begin function first_free_slots
	.p2align	1
	.prefalign	4, .Lfunc_end0, nop
	.type	first_free_slots,@function
first_free_slots:                       # @first_free_slots
# %bb.0:
	l	%r0, 0(%r2)
	ahik	%r1, %r0, -1
	xilf	%r0, 4294967295
	nr	%r0, %r1
	popcnt	%r0, %r0
	sllk	%r1, %r0, 16
	ar	%r0, %r1
	sllk	%r1, %r0, 8
	ar	%r0, %r1
	srl	%r0, 24
	st	%r0, 0(%r3)
	l	%r0, 4(%r2)
	ahik	%r1, %r0, -1
	xilf	%r0, 4294967295
	nr	%r0, %r1
	popcnt	%r0, %r0
	sllk	%r1, %r0, 16
	ar	%r0, %r1
	sllk	%r1, %r0, 8
	ar	%r0, %r1
	srl	%r0, 24
	st	%r0, 4(%r3)
	br	%r14
.Lfunc_end0:
	.size	first_free_slots, .Lfunc_end0-first_free_slots
                                        # -- End function
	.globl	count_permissions               # -- Begin function count_permissions
	.p2align	1
	.prefalign	4, .Lfunc_end1, nop
	.type	count_permissions,@function
count_permissions:                      # @count_permissions
# %bb.0:
	vlrepg	%v0, 0(%r2)
	vpopctf	%v0, %v0
	vstef	%v0, 0(%r3), 0
	vstef	%v0, 4(%r3), 1
	br	%r14
.Lfunc_end1:
	.size	count_permissions, .Lfunc_end1-count_permissions
                                        # -- End function
	.globl	bitset128_size                  # -- Begin function bitset128_size
	.p2align	1
	.prefalign	4, .Lfunc_end2, nop
	.type	bitset128_size,@function
bitset128_size:                         # @bitset128_size
# %bb.0:
	lg	%r1, 8(%r2)
	popcnt	%r1, %r1
	lg	%r0, 0(%r2)
	sllg	%r2, %r1, 32
	agr	%r1, %r2
	sllg	%r2, %r1, 16
	agr	%r1, %r2
	popcnt	%r0, %r0
	sllg	%r2, %r1, 8
	agr	%r1, %r2
	srlg	%r2, %r1, 56
	sllg	%r1, %r0, 32
	agr	%r0, %r1
	sllg	%r1, %r0, 16
	agr	%r0, %r1
	sllg	%r1, %r0, 8
	agr	%r0, %r1
	srlg	%r0, %r0, 56
	agr	%r2, %r0
	br	%r14
.Lfunc_end2:
	.size	bitset128_size, .Lfunc_end2-bitset128_size
                                        # -- End function
	.globl	popcount_u128                   # -- Begin function popcount_u128
	.p2align	1
	.prefalign	4, .Lfunc_end3, nop
	.type	popcount_u128,@function
popcount_u128:                          # @popcount_u128
# %bb.0:
	lg	%r1, 8(%r3)
	popcnt	%r1, %r1
	lg	%r0, 0(%r3)
	sllg	%r3, %r1, 32
	agr	%r1, %r3
	sllg	%r3, %r1, 16
	popcnt	%r0, %r0
	agr	%r1, %r3
	sllg	%r3, %r1, 8
	agr	%r1, %r3
	sllg	%r3, %r0, 32
	agr	%r0, %r3
	sllg	%r3, %r0, 16
	agr	%r0, %r3
	sllg	%r3, %r0, 8
	agr	%r0, %r3
	vgbm	%v0, 0
	srlg	%r1, %r1, 56
	srlg	%r0, %r0, 56
	agr	%r0, %r1
	vlvgg	%v0, %r0, 1
	vst	%v0, 0(%r2), 3
	br	%r14
.Lfunc_end3:
	.size	popcount_u128, .Lfunc_end3-popcount_u128
                                        # -- End function
	.ident	"Apple clang version 21.0.0 (clang-2100.4.1.1)"
	.section	".note.GNU-stack","",@progbits
