# SystemZ bit-count cost experiments (IBM Z)

Runnable experiments for the SystemZ bit-count cost patch (branch
`systemz-bitcount-costs` in `../llvm-project`). Each one compiles small
kernels with the upstream and patched `opt`, then `llc`, for z13–z17, and
times the code with `llvm-mca`.

## Cost Chart for `cttz`
- `<without zero poison> / <with zero poison>`

| | i8 | i16 | i32 | i64 | i128 | 128-bit vector | 256-bit vector |
|---|---|---|---|---|---|---|---|
| z13 | 5 | 8 | 9 | 6 / 4 | 8 | 1 | 2 |
| z14 | 5 | 8 | 9 | 6 / 4 | 6 | 1 | 2 |
| z15 / z16 | 4 | 4 | 4 | 3 | 6 | 1 | 2 |
| z17 | 2 / 1 | 2 / 1 | 2 / 1  | 1  | 1 | 1 | 2 |

Count Trailing Zeros
- There are two possibilities when using CTTZ, first (`i1 false` is `ISD::CTTZ`) is fully defined, when `x = 0` it returns the total number of bits of the type. Second (`i1 true` is `ISD::CTTZ_ZERO_POISON`), relaxes the need to count trailing zeros when `x = 0` (i.e., the total number of bits of the type) and allow returning a poison value, so the compiler assumes that the `x` provided is `!= 0`. 

**`llvm/lib/CodeGen/SelectionDAG/SelectionDAGBuilder.cpp`**
```cpp
case Intrinsic::cttz: {
  SDValue Arg = getValue(I.getArgOperand(0));                               // x 
  ConstantInt *CI = cast<ConstantInt>(I.getArgOperand(1));                  // i1 true/false
  EVT Ty = Arg.getValueType();
  setValue(&I, DAG.getNode(CI->isZero() ? ISD::CTTZ : ISD::CTTZ_ZERO_POISON,// if i1 is false, then use cttz, otherwise, use cttz_zero_poison
                           sdl, Ty, Arg));
  return;
}
```


#### Element width: **128 bits**

- z17: cost 1
  
  ```nasm
  vl    %v0, 0(%r3), 3   # 0 cost, load
  vctzq %v0, %v0         # 1 cost, count trailing zeros
  vst   %v0, 0(%r2), 3   # 0 cost, store
  ```

The above case had `vctzq` to do it in one step. However, < z17 requires expansion:

```cpp
if (!Subtarget.hasVectorEnhancements3()) {
  ...
  setOperationAction(ISD::CTTZ,  MVT::i128, Expand); // < z17
}
```

- Older CPU architectures did not have cttz. It is replaced by `popcount(~x & (x-1))` (I also think this popcount solution not optimal (at least for) SystemZ and should be re-implemented with custom cttz lowering)
**`llvm/lib/CodeGen/SelectionDAG/TargetLowering.cpp:11437`**
```cpp
SDValue Tmp = DAG.getNode(
  ISD::AND, dl, VT,
  DAG.getNOT(dl, Op, VT),                                           // ~x
  DAG.getNode(ISD::SUB, dl, VT, Op, DAG.getConstant(1, dl, VT))     // x-1
);                                                                  // ~x & (x-1)
return DAG.getNode(ISD::CTPOP, dl, VT, Tmp);                        // popcount(~x & (x-1)) : CTPOP which is available between z13-z17
```
- How?
  ```
  x          = ...1011 0100 0000
  x - 1      = ...1011 0011 1111 # -1 will flip the bits up to the first 1 from the RHS
  ~x         = ...0100 1011 1111 # ~ will create a number that forgets the history of numbers to the left and including the first 1 from RHS
  ~x & (x-1) = ...0000 0011 1111 # ANDing these numbers together gives the cttz count
  ```
- The above eventually converts to machine instructions with costs detailed below.

- z14/z15/z16: cost 6
  
  ```nasm
  vl      %v0, 0(%r3), 3  # 0 cost, load
  vgbm    %v1, 65535      # 1 cost, v1 = all ones
  vaq     %v1, %v0, %v1   # 1 cost, v1 = x + (-1) = x - 1
  vnc     %v0, %v1, %v0   # 1 cost, v0 = (x - 1) & ~x   (complement)
  vpopctg %v0, %v0        # 1 cost, popcount of each 64-bit half
  vgbm    %v1, 0          # 1 cost, v1 = 0
  vsumqg  %v0, %v0, %v1   # 1 cost, sum the v2i64 into a v1i128 result
  vst     %v0, 0(%r2), 3  # 0 cost, store
                          # 6 total cost
  ```

- z13: cost 8
  - `vpopctg` doesn't exist on z13 (see `ctpop` lowering explanation below), so it needs to do `vpopct` byte-by-byte.
  - this means, there are two extra operations needed to perform a horizontal sum on the `vpopct` results
  ```nasm
  vl      %v0, 0(%r3), 3  # 0 cost, load
  vgbm    %v1, 65535      # 1 cost, v1 = all ones
  vaq     %v1, %v0, %v1   # 1 cost, v1 = x + (-1) = x - 1
  vnc     %v0, %v1, %v0   # 1 cost, v0 = (x - 1) & ~x   (and-with-complement)
  vpopct  %v0, %v0        # 1 cost, popcount of per each v16i8
  vgbm    %v1, 0          # 1 cost, v1 = 0
  vsumb   %v0, %v0, %v1   # 1 cost, sums v16i8 into a v4i32 result
  vsumgf  %v0, %v0, %v1   # 1 cost, sums v4i32 into a v2i64 result
  vsumqg  %v0, %v0, %v1   # 1 cost, sum the v2i64 into a v1i128 result
  vst     %v0, 0(%r2), 3  # 0 cost, store
                          # 8 total cost
  ```
- Actually, `vsumb` -> `vsumgf` -> `vsumqg` can be simplified to `vsumb` -> `vsumqf` but possibly addressed in another PR for `lowerCTPOP`.

#### Element width: **64 bits**
- (cttz) z13 / z14: 6 cost
  - < z15 there is no and-with-complement (`vnc`) instruction, instead the equivalent expression is used `a & ~b = (a & b) ^ a`
  - this is using a general register unlike the 128-bit case, so `vpopct` can't be used, but also it would take more instructions to use popcount anyways. Instead we have something better, `flogr`.
  - `flogr` can count the number of leading zeros which allows reworking the number trailing zeros by taking `64 - flogr((x-1) & ~x)` based on the expression analyzed earlier.
```nasm
lay %r0, -1(%r2)  # 1 cost, r0 = x - 1
ngr %r2, %r0      # 1 cost, r2 = x & (x-1)
xgr %r2, %r0      # 1 cost, r2 = (x & (x-1)) ^ (x-1) = ~x & (x-1)
flogr %r0, %r2    # 1 cost, r0 = # of leading zeros
lghi %r2, 64      # 1 cost, r2 = 64
sgr %r2, %r0      # 1 cost, r2 = 64 - r0
                  # 6 total cost
```

- (cttz_zero_poison) z13 / z14: 4 cost
  - Using: `flogr(x & -x) ^ 63`
  - When `x = 0`, `x & - x` has `flogr` return 64 and `64 ^ 63 = 127` which is wrong (but OK since zero is poison)
  - When `x != 0`, `flogr(x & -x) ^ 63` returns the same values as `64 - flogr((x-1) & ~x)` cheaper by 2 instructions

```nasm
lcgr  %r0, %r2 # 1 cost, r0 = -x
ngr   %r0, %r2 # 1 cost, r0 = x & -x
flogr %r2, %r0 # 1 cost, r2 = # of leading zeros 
xilf  %r2, 63  # 1 cost, r2 = r2 ^ 63
               # 4 total cost
```

- (cttz and cttz_zero_poison) z15 / z16: 3 cost
    - Using: `popcount((x - 1) & ~x)`
    - z15+ added `ncgrk` (and-with-complement) and `popcnt`
    - Why? `(x - 1) & ~x` gives exactly N ones at the bottom, where N is the trailing zeros count.

```nasm
lay    %r0, -1(%r2)   # 1 cost, r0 = x - 1
ncgrk  %r0, %r0, %r2  # 1 cost, r0 = (x - 1) & ~x    (and-with-complement)
popcnt %r2, %r0, 8    # 1 cost, r2 = # of 1s in r0
                      # 3 total cost
```

- (cttz and cttz_zero_poison) z17: 1 cost
    - Using: `ctzg` at 1 total cost.

#### Element width: **32 bits**

- z13/z14: 9 cost
```nasm
ahik   %r0, %r2, -1          # 1 cost, r0 = x - 1                (32-bit)
xilf   %r2, 4294967295       # 1 cost, r2 = ~x                   (32-bit NOT)
nr     %r0, %r2              # 1 cost, r0 = (x - 1) & ~x         (32-bit AND)
popcnt %r0, %r0              # 1 cost, per-byte 1-bit counts     (M3 = 0)
sllk   %r1, %r0, 16          # 1 cost, \
ar     %r0, %r1              # 1 cost,  | add the 4 low byte counts
sllk   %r1, %r0, 8           # 1 cost,  | into the top byte of the word
ar     %r0, %r1              # 1 cost, /
srlk   %r2, %r0, 24          # 1 cost, shift the top byte down -> result
                             # 9 total cost
```

- z15/z16: 4 cost
```nasm
ahik   %r0, %r2, -1          # 1 cost, r0 = x - 1                (32-bit)
ncrk   %r0, %r0, %r2         # 1 cost, r0 = (x - 1) & ~x         (32-bit and-with-complement)
llgfr  %r0, %r0              # 1 cost, zero the upper 32 bits of the register
popcnt %r2, %r0, 8           # 1 cost, count all 64 bits at once (M3 = 8)
                             # 4 total cost
```

- (cttz) z17:
    - 64 bit is the size required for `ctzg`, `ctzg` needs to be widened so i32 CTTZ is marked `Promote` on z17
```nasm
oihl  %r2, 1               # 1 cost, set bit 32 (stop bit)
ctzg  %r2, %r2             # 1 cost
                           # 2 total cost
```

- (cttz_zero_poison) z17:
```nasm
ctzg  %r2, %r2         # 1 total cost
```

#### Element width: **16 bits**

- (cttz) z17: 2 cost
```nasm
oilh  %r2, 1           # 1 cost, set bit 16 (stop bit)
ctzg  %r2, %r2         # 1 cost
                       # 2 total cost
```
- (cttz_zero_poison) z17: 1 cost
```nasm
ctzg  %r2, %r2 # 1 total cost
```

#### Element width: **8 bits**

- (cttz) z17: 2 cost
```nasm
oill  %r2, 256         # 1 cost, set bit 8  (stop bit)
ctzg  %r2, %r2         # 1 cost
                       # 2 total cost
```
- (cttz_zero_poison) z17: 1 cost
```nasm
ctzg  %r2, %r2 # 1 total cost
```

## Cost Chart for `ctpop` lowering
| | i8 | i16 | i32 | i64 | i128 |
|---|---|---|---|---|---|
| z13 | `N` | `4N` | `2N+1` | `3N+1` | `5` |
| z14+ (VE1) | `N` | `N` | `N` | `N` | `3` |

## How SystemZ handles `ctpop` lowering
- Within the **`lowerCTPOP()`** function in **`llvm/lib/Target/SystemZ/SystemZIselLowering.cpp`**

#### Element width: **128 bits**

```cpp
if (VT.getScalarSizeInBits() == 128) {
  Op = DAG.getNode(ISD::BITCAST, DL, MVT::v2i64, Op);                      // 0 cost, only relabels the bits already in a vector register
  Op = DAG.getNode(ISD::CTPOP, DL, MVT::v2i64, Op);                        // 3 on z13 (calls `lowerCTPOP()` and lowered through "case 64"), 1 on z14+ (`vpopct{b,h,f,g}`)
  SDValue Tmp = DAG.getSplatBuildVector(MVT::v2i64, DL,
                                        DAG.getConstant(0, DL, MVT::i64)); // 1 cost `vgbm` to generate byte mask of all 0's
  Op = DAG.getNode(SystemZISD::VSUM, DL, VT, Op, Tmp);                     // 1 cost `vsumqg` to sum quadword composed of grands
  return Op;                                                               // 5 cost on z13, 3 cost on z14+
}
```
- First, the vector is casted to into `2 lanes` each with width of `64 bits`
- Second, we run `ISD::CTPOP` on the `2 lanes`, depending on the CPU architecture, the legalizer will eventually branch into two options:
  - A: z13 will run **`lowerCTPOP()`** again.
  - B: z14+ will run the vector population count facilities `vpopct{b,h,f,g}`
    - `vpopctb` for `v16i8` (`b` for `byte`)
    - `vpopcth` for `v8i16` (`h` for `halfword`)
    - `vpopctf` for `v4i32` (`f` for `fullword`/`word`)
    - `vpopctg` for `v2i64` (`g` for `doubleword`/`grande`)
  - Why? It is defined in the `SystemZTargetLowering` constructor
    ```cpp
    SystemZTargetLowering::SystemZTargetLowering(const TargetMachine &TM,
                                         const SystemZSubtarget &STI)
      : TargetLowering(TM, STI), Subtarget(STI) {
    ...
      for (MVT VT : MVT::integer_fixedlen_vector_valuetypes()) {
        if (Subtarget.hasVector() && isTypeLegal(VT)) { // v16i8, v8i16, v4i32, v2i64
          ...
          if (Subtarget.hasVectorEnhancements1()) // v14+: vpopct{b,h,f,g}
            setOperationAction(ISD::CTPOP, VT, Legal);
          else
            setOperationAction(ISD::CTPOP, VT, Custom); // v13: lowerCTPOP again
        }
      }
    }
    ```
- Third, we run `SDValue Tmp = DAG.getSplatBuildVector(MVT::v2i64, DL, DAG.getConstant(0, DL, MVT::i64));` which gives `2 lanes` of `64 bits` zero'd.
- Lastly, `Op = DAG.getNode(SystemZISD::VSUM, DL, VT, Op, Tmp);` adds the `2 lanes` of `64 bits` into a single `128 bit` total.
- Overall, `5 * NumVectors` cost on z13 and  `3 * NumVectors` cost on z14+
#### Element width: **8 bits**

```cpp
  if (VT.isVector()) {
    Op = DAG.getNode(ISD::BITCAST, DL, MVT::v16i8, Op);        // 0 cost
    Op = DAG.getNode(SystemZISD::POPCNT, DL, MVT::v16i8, Op);  // 1 cost, SystemZISD::POPCNT gives a base-case for i8 so lowerCTOP() is not called again
    switch (VT.getScalarSizeInBits()) {
    case 8:
      break;
    ...
    default:
      llvm_unreachable("Unexpected type");
    }
    return Op;                                                 // 1 total cost
  }
```
- First, bitcast `16 lanes` of `8 bits`
- Second, call the `SYSTEMZISD::POPCNT` to get the population count.
- **Performance Note**: This total cost should be `1 * NumVectors`. The upstream records it as `2 * NumVectors` for z13 from the generic cost impl. because it is labelled `Custom`.
  - Remember, `setOperationAction(ISD::CTPOP, VT, Custom); // z13: lowerCTPOP again` labels it as `Custom`. Then, the generic cost impl. reports custom events as `LT.first * 2` in **`llvm/include/llvm/CodeGen/BasicTTIImpl.h`**
  ```cpp
  } else if (!BitManipSplit && TLI->isOperationCustom(ISD, LT.second)) {
    // If the operation is custom lowered then assume
    // that the code is twice as expensive.
    return (LT.first * 2);
  }
  ```

#### Element width: **16 bits**

```cpp
  if (VT.isVector()) {
    Op = DAG.getNode(ISD::BITCAST, DL, MVT::v16i8, Op);                          // 0 cost
    Op = DAG.getNode(SystemZISD::POPCNT, DL, MVT::v16i8, Op);                    // 1 cost, `vpopct %v0, %v24, 0`
    switch (VT.getScalarSizeInBits()) {
    case 16: {
      Op = DAG.getNode(ISD::BITCAST, DL, VT, Op);                                // 0 cost
      SDValue Shift = DAG.getConstant(8, DL, MVT::i32);                          // 0 cost, get a constant `8`
      SDValue Tmp = DAG.getNode(SystemZISD::VSHL_BY_SCALAR, DL, VT, Op, Shift);  // 1 cost, `veslh %v1, %v0, 8` - shift left by 8
      Op = DAG.getNode(ISD::ADD, DL, VT, Op, Tmp);                               // 1 cost, `vah %v0, %v0, %v1` - adds 
      Op = DAG.getNode(SystemZISD::VSRL_BY_SCALAR, DL, VT, Op, Shift);           // 1 cost, `vesrlh %v24, %v0, 8` - revert the shift left
      break;
    }
    ...
    default:
      llvm_unreachable("Unexpected type");
    }
    return Op;                                                                    // 4 total cost
  }
```
- 1. bitcast at 0 cost
  2. Stores the count result in `%v0`. `SystemZISD::POPCNT` gives a base-case for i8 so `lowerCTOP()` is not called again
  3. bitcast at 0 cost
  4. retrieve constant 8 at 0 cost
  5. Shift left by 8 to isolate the second byte. (`0x0402` -> `0x02`)
  6. Add the original popcnt result (`0x02 + 0x0402 = 0x0602`)
  7. Revert the shift left to produce the final result (`0x0602 >> 8 = 0x0006`) then store this into a vector register `%v24`.
- **Performance Note**: This total cost should be `4 * NumVectors`. The upstream reports `2 * NumVector` for the similar reason as the above example. This total cost only applies to z13 because z14+ has it's own vector facilities. This means the upstream is currently understating the cost it takes to run POPCNT for v8i16 on v13.

#### Element width: **32 bits**

```cpp
  if (VT.isVector()) {
    Op = DAG.getNode(ISD::BITCAST, DL, MVT::v16i8, Op);                          // 0 cost
    Op = DAG.getNode(SystemZISD::POPCNT, DL, MVT::v16i8, Op);                    // 1 cost, `vpopct %v0, %v24, 0`
    switch (VT.getScalarSizeInBits()) {
    case 32: {
      SDValue Tmp = DAG.getSplatBuildVector(MVT::v16i8, DL,
                                            DAG.getConstant(0, DL, MVT::i32));   // 1 shared cost due to machine LICM, `vgbm %v1, 0` 
      Op = DAG.getNode(SystemZISD::VSUM, DL, VT, Op, Tmp);                       // 1 cost, `vsumb %v24, %v0, %v1` adds 4 byte counts in each 32 bit lane
                                                                                 //   - A composition of:
                                                                                 //         `vpopct %v0, %v24, 0`  produces Op
                                                                                 //         `vgbm %v1, 0` produces Tmp
                                                                                 //         `vsumb %v24, %v0, %v1` produces the final v4i32 result summed across individual "b" - bytes v16i8
      break;                                                                     //   - VT: v4i32, Op: v16i8, Tmp: v16i8
    }
    ...
    default:
      llvm_unreachable("Unexpected type");
    }
    return Op;                                                                    // 2 total cost + 1 shared cost of `vgbm %v1, 0` 
  }
```
- 1. bitcast at 0 cost
  2. Stores the count result in `%v0`. `SystemZISD::POPCNT` gives a base-case for i8 so `lowerCTOP()` is not called again
  3. The constant 0 is stored into register `%v1`. It gets hoisted by the compiler so its a `O(1)` cost.
  4. Sums two v16i8 elements (collapses 4-byte entries together) into a v4i32 result at cost 1.
- **Performance Note**: This total cost should be `2 * NumVectors + 1`. The upstream reports `2 * NumVectors` for the similar reason as the above example. This total cost only applies to z13 because z14+ has it's own vector facilities.

#### Element width: **64 bits**

```cpp
  if (VT.isVector()) {
    Op = DAG.getNode(ISD::BITCAST, DL, MVT::v16i8, Op);                          // 0 cost
    Op = DAG.getNode(SystemZISD::POPCNT, DL, MVT::v16i8, Op);                    // 1 cost, `vpopct %v0, %v24, 0`
    switch (VT.getScalarSizeInBits()) {
    case 64: {
      SDValue Tmp = DAG.getSplatBuildVector(MVT::v16i8, DL,
                                            DAG.getConstant(0, DL, MVT::i32));   // 1 shared cost due to machine LICM, `vgbm %v1, 0` 
      Op = DAG.getNode(SystemZISD::VSUM, DL, MVT::v4i32, Op, Tmp);               // 1 cost, `vsumb %v0, %v0, %v1` adds two v16i8 to get a v4i32 result
      Tmp = DAG.getNode(ISD::BITCAST, DL, MVT::v4i32, Tmp);                      // 0 cost
      Op = DAG.getNode(SystemZISD::VSUM, DL, VT, Op, Tmp);                       // 1 cost, `vsumgf %24, %v0, %v1` adds two v4i32 to get a v2i64 result
      break;
    }
    ...
    default:
      llvm_unreachable("Unexpected type");
    }
    return Op;                                                                    // 3 total cost + 1 shared cost of `vgbm %v1, 0` 
  }
```
- 1. bitcast at 0 cost
  2. Stores the count result in `%v0`.
  3. The constant 0 is stored into register `%v1`. It gets hoisted by the compiler so its a `O(1)` cost.
  4. Sums two v16i8 elements (collapses 4 byte-adjacent entries together) into a v4i32 result at cost 1.
  5. bitcast at 0 cost
  6. Sums two v4i32 elements (collapses 2 32bit-adjacent entries together) into a v2i64 result at cost 1.
- **Performance Note**: This total cost should be `3 * NumVectors + 1`. The upstream reports `2 * NumVectors` for the similar reason as the above example. This total cost only applies to z13 because z14+ has it's own vector facilities.
- 
#### Code Reference:
```cpp
SDValue SystemZTargetLowering::lowerCTPOP(SDValue Op,
                                          SelectionDAG &DAG) const {
  EVT VT = Op.getValueType();
  SDLoc DL(Op);
  Op = Op.getOperand(0);

  if (VT.getScalarSizeInBits() == 128) {
    Op = DAG.getNode(ISD::BITCAST, DL, MVT::v2i64, Op);
    Op = DAG.getNode(ISD::CTPOP, DL, MVT::v2i64, Op);
    SDValue Tmp = DAG.getSplatBuildVector(MVT::v2i64, DL,
                                          DAG.getConstant(0, DL, MVT::i64));
    Op = DAG.getNode(SystemZISD::VSUM, DL, VT, Op, Tmp);
    return Op;
  }

  // Handle vector types via VPOPCT.
  if (VT.isVector()) {
    Op = DAG.getNode(ISD::BITCAST, DL, MVT::v16i8, Op);
    Op = DAG.getNode(SystemZISD::POPCNT, DL, MVT::v16i8, Op);
    switch (VT.getScalarSizeInBits()) {
    case 8:
      break;
    case 16: {
      Op = DAG.getNode(ISD::BITCAST, DL, VT, Op);
      SDValue Shift = DAG.getConstant(8, DL, MVT::i32);
      SDValue Tmp = DAG.getNode(SystemZISD::VSHL_BY_SCALAR, DL, VT, Op, Shift);
      Op = DAG.getNode(ISD::ADD, DL, VT, Op, Tmp);
      Op = DAG.getNode(SystemZISD::VSRL_BY_SCALAR, DL, VT, Op, Shift);
      break;
    }
    case 32: {
      SDValue Tmp = DAG.getSplatBuildVector(MVT::v16i8, DL,
                                            DAG.getConstant(0, DL, MVT::i32));
      Op = DAG.getNode(SystemZISD::VSUM, DL, VT, Op, Tmp);
      break;
    }
    case 64: {
      SDValue Tmp = DAG.getSplatBuildVector(MVT::v16i8, DL,
                                            DAG.getConstant(0, DL, MVT::i32));
      Op = DAG.getNode(SystemZISD::VSUM, DL, MVT::v4i32, Op, Tmp);
      Tmp = DAG.getNode(ISD::BITCAST, DL, MVT::v4i32, Tmp);
      Op = DAG.getNode(SystemZISD::VSUM, DL, VT, Op, Tmp);
      break;
    }
    default:
      llvm_unreachable("Unexpected type");
    }
    return Op;
  }
```
