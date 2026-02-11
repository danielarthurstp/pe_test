# pe_fp32 — Specification (spec.md)

## 1. Overview

`pe_fp32` implements a **5-lane FP32 dot-product** (multiply-accumulate across 5 FP32 lanes) using a **3-stage pipelined datapath** and a **2-cycle time-multiplexed segmented multiplier** per lane.

At a high level:

\[
out \approx \text{RoundFP32}\left(\sum_{i=0}^{4} (A_i \times B_i)\right)
\]

Where each `A_i` and `B_i` is an IEEE-754 binary32 value extracted from the packed input buses.

> Important: This module is **not a fully IEEE-754 compliant FP32 FMA**. It is a segmented-multiply + align + integer-accumulate + normalize/round pipeline that aims to match a “round-at-the-end” style accumulation for many cases, but it does not explicitly implement all IEEE corner cases (NaN/Inf/subnormals/etc).

---

## 2. Module Interface

```verilog
module pe_fp32(
    input  [159:0] A,
    input  [159:0] B,
    input          clk,
    input          clk_cntr,
    output reg [31:0] out
);

2.1 Packed lane format

A and B each contain 5 lanes of FP32, packed little-lane-first:
	•	Lane 0: bits [31:0]
	•	Lane 1: bits [63:32]
	•	Lane 2: bits [95:64]
	•	Lane 3: bits [127:96]
	•	Lane 4: bits [159:128]

Each lane is IEEE-754 binary32: {sign[31], exp[30:23], mant[22:0]}.

⸻

3. Functional Behavior

3.1 Intended computation

For each lane i:
	•	Extract sign/exponent/mantissa.
]	•	Multiply mantissas via segmented multiplication across two cycles (clk_cntr driven).
	•	Align partial products based on exponent compare logic.
	•	Reduce via adder tree + optional accumulation (also time-muxed).
	•	Normalize and round to produce FP32 output.

3.2 “Round only at the end” reference model

The testbench you’re using models the expected value as:
	1.	Inputs are quantized to FP32 (pack/unpack)
	2.	Products are accumulated in higher precision (Python float / FP64)
	3.	Final result quantized once to FP32

This corresponds to a MAC-style accumulation with a single rounding at the end, not FP32 multiply-add with rounding after each op.

⸻

4. Timing / Control (clk_cntr protocol)

4.1 Time-multiplex phases

clk_cntr must be driven in a 2-cycle sequence per operation:
	•	Phase 0: clk_cntr = 0 for one rising edge of clk
	•	Phase 1: clk_cntr = 1 for one rising edge of clk
	•	Then return to idle (clk_cntr = 0)

Internally, the design pipelines this as:
	•	clk_cntr_stage1 <= clk_cntr
	•	clk_cntr_stage2 <= clk_cntr_stage1
	•	clk_cntr_stage3 <= clk_cntr_stage2

4.2 Output latency

The current cocotb driver assumes:
	•	You apply Phase0 edge then Phase1 edge.
	•	Then you wait 4 additional rising edges
	•	Then out is sampled.

So “effective check time” is Phase1 + 4 cycles.

⸻

5. Supported / Unsupported IEEE-754 Cases

5.1 Explicitly handled
	•	Zero-ish behavior: when sum_f == 0, exponent is forced to 0 and mantissa becomes 0.
	•	RNE rounding in the final pack stage.

5.2 Not explicitly specified / may be incorrect
	•	NaN propagation
	•	+/-Infinity behavior
	•	Subnormals (input or output)
	•	Overflow/underflow behavior (saturation to Inf, flush-to-zero, etc.)
	•	Exact IEEE exception flags

Your cocotb testbench has already been updated to avoid generating Inf and to avoid overflow in stimulus. That matches the “finite-only” expectation.

⸻

6. Verification Notes (based on your cocotb tests)

6.1 Required handshake

The testbench assumes exactly:
	•	Drive A, B
	•	clk_cntr=0 for 1 cycle, then clk_cntr=1 for 1 cycle
	•	Wait 4 cycles
	•	Compare out

6.2 Comparing +0 vs -0

Because the RTL may produce -0 (0x80000000) when the sum cancels to zero, the spec treats +0 and -0 as equivalent unless you explicitly require “sign-of-zero = 0”.

If you want the RTL to never output -0, you should override sign when sum is zero:
	•	if (sum_f == 0) outt[31] = 1'b0;

Otherwise, the testbench should compare zeros as equal.

⸻

7. Acceptance Criteria

A build of pe_fp32 is considered compliant with this spec if:
	1.	For finite normal FP32 inputs (no NaN/Inf/subnormals), it matches the testbench reference model:
	•	inputs quantized to FP32
	•	accumulate in high precision
	•	round once at the end to FP32
	2.	It follows the defined clk_cntr 2-cycle protocol.
	3.	It produces either +0 or -0 when the mathematical result is zero (unless the design is updated to force +0).
