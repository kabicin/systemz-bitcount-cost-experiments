# SystemZ bit-count cost toys (IBM Z)

Runnable experiments for the SystemZ bit-count cost patch (branch
`systemz-bitcount-costs` in `../llvm-project`). Each one compiles small
kernels with the upstream and patched `opt`, then `llc`, for z13–z17, and
times the code with `llvm-mca`.

## How SystemZ handles CTPOP lowering
- Within the **`lowerCTPOP()`** function in **`llvm/lib/Target/SystemZ/SystemZIselLowering.cpp`**
  - Element width: **128 bits**
    ```cpp
    if (VT.getScalarSizeInBits() == 128) {
      Op = DAG.getNode(ISD::BITCAST, DL, MVT::v2i64, Op);
      Op = DAG.getNode(ISD::CTPOP, DL, MVT::v2i64, Op);
      SDValue Tmp = DAG.getSplatBuildVector(MVT::v2i64, DL,
                                            DAG.getConstant(0, DL, MVT::i64));
      Op = DAG.getNode(SystemZISD::VSUM, DL, VT, Op, Tmp);
      return Op;
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

Code Reference:
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
