	.file	"examples.cpp"
	.text
	.globl	first_free_slots                # -- Begin function first_free_slots
	.p2align	1
	.prefalign	4, .Lfunc_end0, nop
	.type	first_free_slots,@function
first_free_slots:                       # @first_free_slots
# %bb.0:
	vlrepg	%v0, 0(%r2)
	vctzf	%v0, %v0
	vstef	%v0, 0(%r3), 0
	vstef	%v0, 4(%r3), 1
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
	vpopct	%v0, %v0, 0
	vgbm	%v1, 0
	vsumb	%v0, %v0, %v1
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
	vlrepg	%v0, 0(%r2)
	vleg	%v0, 8(%r2), 1
	vpopct	%v0, %v0, 0
	vgbm	%v1, 0
	vsumb	%v0, %v0, %v1
	vsumgf	%v0, %v0, %v1
	vrepg	%v1, %v0, 0
	vag	%v0, %v1, %v0
	vlgvg	%r2, %v0, 1
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
	vlrepg	%v0, 0(%r3)
	vleg	%v0, 8(%r3), 1
	vpopct	%v0, %v0, 0
	vgbm	%v1, 0
	vsumb	%v0, %v0, %v1
	vsumgf	%v0, %v0, %v1
	vrepg	%v2, %v0, 0
	vag	%v0, %v2, %v0
	vlgvg	%r0, %v0, 1
	vlvgg	%v1, %r0, 1
	vst	%v1, 0(%r2), 3
	br	%r14
.Lfunc_end3:
	.size	popcount_u128, .Lfunc_end3-popcount_u128
                                        # -- End function
	.ident	"Apple clang version 21.0.0 (clang-2100.4.1.1)"
	.section	".note.GNU-stack","",@progbits
