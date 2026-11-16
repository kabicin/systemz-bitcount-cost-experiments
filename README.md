# SystemZ bit-count cost experiments (IBM Z)

Runnable experiments for the SystemZ bit-count cost patch (branch
`systemz-bitcount-costs` in `../llvm-project`). Each one compiles small
kernels with the upstream and patched `opt`, then `llc`, for z13–z17, and
times the code with `llvm-mca`.

## Cost Chart for CTPOP lowering
| | i8 | i16 | i32 | i64 | i128 |
|---|---|---|---|---|---|
| z13 | `N` | `4N` | `2N+1` | `3N+1` | `5` |
| z14+ (VE1) | `N` | `N` | `N` | `N` | `3` |

## Cost Chart for i128 CTPOP lowering



## How SystemZ handles CTPOP lowering
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
