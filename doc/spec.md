# pe_fp32 — Specification

This document specifies **exactly how `sources/pe_fp32.sv` is structured** and **what connects to what**, so an AI can regenerate the RTL and correctly instantiate the provided submodules.

---

## 1. Overview

`pe_fp32` computes a **5‑lane FP32 dot product**:

`out = RoundFP32( Σ_{i=0..4} (A[i] × B[i]) )`

- Inputs `A` and `B` each pack 5 IEEE‑754 binary32 lanes.
- Datapath uses a **2‑phase (clk_cntr) segmented multiply** per lane, producing **10 partial products** (2 per lane).
- Partial products are exponent‑aligned, sign‑extended, reduced in an adder tree, normalized, and rounded (RNE).

> Note: This is **not full IEEE‑754** (no NaN/Inf/subnormal rules are guaranteed). It is designed to satisfy the provided cocotb tests and deterministic synthesis under Icarus.

---

## 2. Top-Level Interface

```verilog
module pe_fp32(
    input  [159:0] A,
    input  [159:0] B,
    input          clk,
    input          clk_cntr,
    output reg [31:0] out
);
```

### 2.1 Lane packing

| Lane | A bits | B bits |
|---:|---|---|
| 0 | A[31:0] | B[31:0] |
| 1 | A[63:32] | B[63:32] |
| 2 | A[95:64] | B[95:64] |
| 3 | A[127:96] | B[127:96] |
| 4 | A[159:128] | B[159:128] |

Each lane is IEEE‑754 binary32 `{sign[31], exp[30:23], frac[22:0]}`.

---

## 3. Timing / Control (clk_cntr protocol)

### 3.1 Two-phase operation

Each dot-product operation is launched by driving `clk_cntr` as:

| Cycle | clk_cntr | Meaning |
|---:|:---:|---|
| Phase 0 | 0 | compute partial products for segment combination #0 |
| Phase 1 | 1 | compute partial products for segment combination #1 |

Then `clk_cntr` returns to 0 (idle). Internally `pe_fp32` pipelines this control:

- `clk_cntr_stage1 <= clk_cntr`
- `clk_cntr_stage2 <= clk_cntr_stage1`
- `clk_cntr_stage3 <= clk_cntr_stage2`

### 3.2 Output latency (as assumed by tests)

The cocotb driver samples `out` **4 cycles after Phase 1** (i.e., after the phase‑1 edge plus 4 additional rising edges).

---

## 4. Datapath breakdown (how pe_fp32.sv is divided)

`pe_fp32.sv` is organized into **three pipeline stages**, plus control pipelining:

| Stage | When active | What it does | Major outputs |
|---:|---|---|---|
| Stage 1 | `clk_cntr_stage1` | decode lanes, form segmented multiplier inputs, compute per-lane exponent/sign, and generate 10 partial products (2 per lane) | `product[0..9]`, `d_exp0..d_exp4`, `ssign0..ssign4` |
| Stage 2 | combinational, gated by `clk_cntr_stage2` | exponent compare (max + diffs), align 10 partial products, sign-extend, accumulate via adder-tree (with optional feedback accumulator) | `ssum`, `mmax_exp_s2`, `sign_final` |
| Stage 3 | combinational, gated by `clk_cntr_stage3` | magnitude + leading-zero detect, normalize, RNE rounding, pack FP32 | `outt` (registered to `out`) |

---

## 5. Signal naming conventions (key internal buses)

### 5.1 Per-lane decoded fields (stage 1 registers)

| Signal | Width | Meaning |
|---|---:|---|
| `mant_A32[i]`, `mant_B32[i]` | 23 | fraction field of FP32 lane i |
| `exp_A32[i]`, `exp_B32[i]` | 8 | exponent field |
| `sign_A32[i]`, `sign_B32[i]` | 1 | sign bit |

### 5.2 Per-lane product meta

| Signal | Width | Meaning |
|---|---:|---|
| `ssign{i}` | 1 | product sign for lane i (`sign_A32[i] ^ sign_B32[i]`) |
| `d_exp{i}` | 9 | unbiased product exponent for lane i (`exp_A + exp_B - 127`) |
| `mmax_exp_s2` | 9 | max exponent across lanes (from `CEC`) |

### 5.3 Partial products and alignment

| Signal | Width | Meaning |
|---|---:|---|
| `a0..a9`, `b0..b9` | 12 | segmented multiplier operands |
| `product[0..9]` | 24 | 12×12 partial product outputs |
| `p_shift[k]` | 6 | left shift amount to position segment product (0/12/24) |
| `d_ddiff[k]` | 9 | exponent-difference shift (right shift) for alignment |
| `as_out[k]` | 61 | aligned, sign-extended partial product |

---

## 6. Functional behavior (what math is implemented)

### 6.1 Segmented multiplication (2 partial products per lane)

Each FP32 significand is treated as `1.frac` (24-bit) and split into two 12-bit chunks:

- High chunk: `{1'b1, frac[22:12]}` (12 bits)
- Low chunk: `frac[11:0]` (12 bits)

For each lane, `pe_fp32` generates **two** 12×12 multiplications across the two phases, resulting in two partial products per lane. These are later shifted by `p_shift` (0/12/24) and summed.

> Exact mapping is implemented in Stage 1 in `pe_fp32.sv` by assigning `a0..a9`, `b0..b9`, `p_shift[0..9]` depending on `clk_cntr_stage1`.

### 6.2 Exponent compare and alignment

- A single reference exponent `mmax_exp_s2` is computed across lanes.
- For each lane i, `diff_i = mmax_exp_s2 - d_exp_i`.
- Each lane’s two partial products inherit the same `diff_i` (mapped into `d_ddiff[2*i]` and `d_ddiff[2*i+1]`).
- Alignment shifts are performed by `Alignment_Shifter`:
  - Left shift by `p_shift[k]` (segment placement)
  - Right shift by `d_ddiff[k]` (exponent alignment)

### 6.3 Signed accumulation

Each aligned partial product is sign-extended and summed using the adder tree.
- The adder tree takes a sign bit per term (`s_sign[k]`).
- A feedback accumulator `acc_in` is set to:
  - `0` when `clk_cntr_stage2 == 0`
  - previous sum (`ssum`) when `clk_cntr_stage2 == 1`

This allows the phase‑0 partials to be summed first, then phase‑1 partials added in the next step.

### 6.4 Normalize and round (Stage 3)

- Convert final signed sum to magnitude + sign.
- Use `LZD` to find leading zeros → compute a normalization shift (`position`).
- Normalize (`sum_f << position`), then pack:
  - exponent update from `mmax_exp_s2` and `position`
  - mantissa field with **RNE rounding** using Guard/ Round/ Sticky bits
- If sum is 0 → exponent forced to 0.

---

## 7. Verification expectations (what the cocotb tests are checking)

- Only **finite normal** FP32 values are expected (testbench avoids Inf/overflow).
- Handshake:
  1. Drive `A`, `B`
  2. Phase0 (`clk_cntr=0`) one cycle
  3. Phase1 (`clk_cntr=1`) one cycle
  4. Wait 4 cycles
  5. Compare `out` to reference model (accumulate in high precision, round once at end)

---

## 8. Submodules (purpose, interface, and top-level connections)

This section explains **each provided RTL submodule** and **exactly how `pe_fp32.sv` connects to it**.

### 8.1 `multi12bX12b` — 12×12 segmented multiplier

**Purpose:** Produces a 24-bit partial product used for segmented significand multiplication.

**Interface:**
```verilog
module multi12bX12b(
    input  [11:0] a,
    input  [11:0] b,
    output [23:0] product
);
```

**Top-level connections (10 instances):**

| Instance | a input | b input | product output |
|---:|---|---|---|
| m0 | `a0` | `b0` | `product[0]` |
| m1 | `a1` | `b1` | `product[1]` |
| ... | ... | ... | ... |
| m9 | `a9` | `b9` | `product[9]` |

**How `a0..a9` / `b0..b9` are formed:**
- Derived from lane significand chunks (high/low 12-bit pieces).
- Selected by `clk_cntr_stage1` to realize the 2‑phase segmented multiply schedule.

---

### 8.2 `CEC` — exponent compare / difference generator

**Purpose:** Computes:
- `max_exp`: maximum of the 5 per-lane product exponents
- `diff_0..diff_4`: per-lane difference to `max_exp` for alignment

**Interface:**
```verilog
module CEC(
    input  [8:0] exp0, exp1, exp2, exp3, exp4,
    input  [7:0] exp5, exp6, exp7, exp8, exp9, // unused by pe_fp32 (tie to 0)
    output [8:0] max_exp,
    output [8:0] diff_0, diff_1, diff_2, diff_3, diff_4,
    output [7:0] diff_5, diff_6, diff_7, diff_8, diff_9
);
```

**Top-level instantiation:**
```verilog
CEC ec1(
  .exp0(d_exp0), .exp1(d_exp1), .exp2(d_exp2), .exp3(d_exp3), .exp4(d_exp4),
  .exp5(8'b0), .exp6(8'b0), .exp7(8'b0), .exp8(8'b0), .exp9(8'b0),
  .max_exp(mmax_exp_s2),
  .diff_0(diff_0), .diff_1(diff_1), .diff_2(diff_2), .diff_3(diff_3), .diff_4(diff_4),
  .diff_5(), .diff_6(), .diff_7(), .diff_8(), .diff_9()
);
```

**Usage in top:**
- `mmax_exp_s2` is used in Stage 3 exponent packing.
- `diff_0..diff_4` are copied into `d_ddiff[0..9]` (two per lane).

---

### 8.3 `Alignment_Shifter` — align a partial product

**Purpose:** Applies two shifts to each partial product:
1. `p_shift` (left shift): places the segment partial in the proper bit position
2. `diff` (right shift): aligns all lanes to the common exponent `max_exp`

**Interface:**
```verilog
module Alignment_Shifter(
    input  [23:0] n,
    input  [8:0]  diff,
    input  [5:0]  p_shift,
    output [60:0] out
);
```

**Top-level connections (10 instances):**

| Instance | n | diff | p_shift | out |
|---:|---|---|---|---|
| as0 | `product[0]` | `d_ddiff[0]` | `p_shift[0]` | `as_out[0]` |
| ... | ... | ... | ... | ... |
| as9 | `product[9]` | `d_ddiff[9]` | `p_shift[9]` | `as_out[9]` |

---

### 8.4 `Adder_Tree` — multi-input signed reduction with optional accumulator

**Purpose:** Sums 10 signed partial products and an optional accumulator term.

**Interface:**
```verilog
module Adder_Tree(
    input  [63:0] n1, n2, n3, n4, n5, n6, n7, n8, n9, n10,
    input  [9:0]  sign,
    input  [65:0] acc_in,
    output [65:0] sum,
    output        sign_final
);
```

**Top-level connections:**
- `n1..n10` are derived from `as_out[k]` with sign-extension to 64 bits:
  - `n1 = {as_out[0][60], as_out[0][60], as_out[0][60], as_out[0]}`
  - ...
  - `n10 = {as_out[9][60], as_out[9][60], as_out[9][60], as_out[9]}`
- `sign[0..9]` is `s_sign[0..9]` (two copies per lane sign)
- `acc_in` is `0` on phase0, otherwise previous `ssum` on phase1
- outputs:
  - `sum -> ssum`
  - `sign_final -> sign_final`

---

### 8.5 `LZD` — leading-zero detector for normalization

**Purpose:** Finds the number of leading zeros in the 64-bit magnitude of the final sum so the result can be normalized.

**Interface:**
```verilog
module LZD(
    input  [63:0] in,
    output [5:0]  out
);
```

**Top-level connection:**
- `in` is `sum_f[63:0]` where `sum_f` is the magnitude of the signed sum.
- `out` is `position` (normalization shift count).

---

### 8.6 `CLA_AdderTree`, `csla`, `compressor7to2`

These files provide **adder/compressor building blocks** used internally by `Adder_Tree.v` (and can be reused by alternative architectures).

#### `compressor7to2`
- Purpose: compress 7 input bits into sum/carry bits (partial product reduction primitive).
```verilog
module compressor7to2(input [6:0] A, output [1:0] s, output [1:0] c);
```

#### `csla`
- Purpose: carry-select style adder primitive used in reduction.
```verilog
module csla(input [15:0] in0, input [15:0] in1, output [16:0] sum);
```

#### `CLA_AdderTree`
- Purpose: 24×24 multiply-like adder-tree used inside some reduction implementations.
```verilog
module CLA_AdderTree(
    input  [23:0] in1, input [23:0] in2,
    output [47:0] sum
);
```

> In the provided `pe_fp32.sv`, these are **not instantiated directly**; they exist to support the implementation of `Adder_Tree.v` (or to be used by future refactors).

---

## 9. Acceptance criteria

An implementation is compliant if:

1. It compiles and runs under **Icarus + cocotb** (no SVA properties/sequences).
2. It follows the **2-phase clk_cntr protocol**.
3. It produces deterministic outputs (no X/Z at sample time).
4. It matches the cocotb reference model for finite normal inputs (round at the end).

