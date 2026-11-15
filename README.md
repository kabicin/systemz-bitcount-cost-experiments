# SystemZ bit-count cost experiments (IBM Z)

Runnable experiments for the SystemZ bit-count cost patch (branch
`systemz-bitcount-costs` in `../llvm-project`). Each one compiles small
kernels with the upstream and patched `opt`, then `llc`, for z13–z17, and
times the code with `llvm-mca`.

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
  - **Performance Note**: This total cost should be 1. The upstream records it as 2 from the generic cost impl. because it is labelled `Custom`.
    - Remember, `setOperationAction(ISD::CTPOP, VT, Custom); // z13: lowerCTPOP again` labels it as `Custom`. Then, the generic cost impl. reports custom events as `LT.first * 2` in **`llvm/include/llvm/CodeGen/BasicTTIImpl.h`**
    ```cpp
    } else if (!BitManipSplit && TLI->isOperationCustom(ISD, LT.second)) {
      // If the operation is custom lowered then assume
      // that the code is twice as expensive.
      return (LT.first * 2);
    }
    ```


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
