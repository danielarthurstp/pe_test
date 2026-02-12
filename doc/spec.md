# pe_fp32 specification

This document specifies how the `pe_fp32` processing element computes a **5-lane FP32 dot product** in a synthesizable RTL design using the provided submodules.

> Goal:  
> `out = Σ_{i=0..4} (A[i] * B[i])` where `A` and `B` each contain 5 packed IEEE-754 single-precision values.

## 1. Top-Level Interface

### 1.1 Module
- **Top file:** `sources/pe_fp32.sv`
- **Top module:** `pe_fp32`

### 1.2 Ports
- `input  [159:0] A` : packed FP32 lanes `A[0]..A[4]`
- `input  [159:0] B` : packed FP32 lanes `B[0]..B[4]`
- `input          clk`
- `input          clk_cntr` : phase/control signal (implementation-defined)
- `output reg [31:0] out` : FP32 dot-product output

### 1.3 Lane Packing
Each lane is 32 bits:
- `lane0 = bus[31:0]`
- `lane1 = bus[63:32]`
- `lane2 = bus[95:64]`
- `lane3 = bus[127:96]`
- `lane4 = bus[159:128]`

## 2. FP32 Format
IEEE-754 single-precision:
- `sign` : 1 bit
- `exp`  : 8 bits (bias 127)
- `mant` : 23 bits (fraction)
- For normalized numbers: significand = `1.mant`
- For subnormals (exp==0): significand = `0.mant`

This PE is intended to be synthesizable and compatible with Icarus Verilog for simulation.

## 3. Computation Overview

The dot product is computed in these logical phases:

1. **Decode** each FP32 input lane: sign, exponent, mantissa.
2. **Multiply** per-lane significands and compute per-lane exponents/signs:
   - `sign_prod[i] = sign_A[i] XOR sign_B[i]`
   - `exp_prod[i]  = exp_A[i] + exp_B[i] - 127`
   - `mant_prod[i] = sig_A[i] * sig_B[i]`
3. **Exponent comparison** across lanes to choose an alignment reference exponent:
   - `exp_max = max(exp_prod[i])` for all valid lanes
4. **Alignment**: shift each partial product based on exponent difference:
   - `shift[i] = exp_max - exp_prod[i]`
   - `aligned[i] = mant_prod[i] >> shift[i]` (right shift for smaller exponents)
5. **Signed accumulation** using two's-complement based on product sign:
   - If `sign_prod[i]==1`: `signed_pp[i] = two_complement(aligned[i])`
   - Else: `signed_pp[i] = aligned[i]`
6. **Adder tree reduction** to sum all `signed_pp[i]` to a wide accumulator.
7. **Leading-one detection** on the magnitude of the sum.
8. **Normalize** (shift and adjust exponent), **round** RNE, and **pack** the final FP32 output.

## 4. Detailed Processing Steps

### 4.0 Pipeline timeline

| Cycle (clk edge) | `clk_cntr` phase | Main computations | Output / registered state |
|---:|:---:|---|---|
| 1 | Phase 0 (`0`) | **First segmented multiply pass (low-side)** per lane: compute `a_hi*b_lo` and `a_lo*b_lo`, form `pp0`. In parallel: compute `sign_prod[i]` and `exp_prod[i]`. Compute `exp_ref` (e.g., max of `exp_prod[i]`) and per-lane shift `sh0[i] = exp_ref - exp_prod[i]`. | Register `pp0_aligned_mag[i] = (pp0 >> sh0[i])`, `sign_prod[i]`, and `exp_ref`. |
| 2 | Phase 1 (`1`) | **(a)** Apply sign to the aligned `pp0`: if `sign_prod[i]=1` take two’s complement of `pp0_aligned_mag[i]` to form `pp0_signed[i]`. **(b)** Second segmented multiply pass (high-side): compute `a_hi*b_hi` and `a_lo*b_hi`, form `pp1`. **(c)** Alignment shift for `pp1` is computed by **CEC (exponent comparator)**, producing `sh1[i]` and `pp1_aligned_mag[i] = (pp1 >> sh1[i])`. | Register partial sum `sum0 = Σ(pp0_signed[i])` and register `pp1_aligned_mag[i]` (plus `sign_prod[i]` for reuse). |
| 3 | Idle (`0`) | Apply sign to `pp1_aligned_mag[i]`: if `sign_prod[i]=1`, take two’s complement to form `pp1_signed[i]`. Then accumulate: `sum1 = sum0 + Σ(pp1_signed[i])`. | Register **final signed accumulator** `sum_final = sum1`. |
| 4 | Idle (`0`) | **Final pack stage:** (1) If `sum_final` is negative, take two’s complement to get magnitude and set output sign. (2) Run `LZD` (leading-one/leading-zero detection) on the magnitude to find the normalization shift. (3) Normalize (shift magnitude) and adjust exponent: `exp_out = exp_ref + exp_adjust`. (4) Round (RNE if implemented) and pack `{sign, exp, mant}`. | Drive `out` with the packed FP32 result. `out` is **valid at this cycle** (matches the cocotb “Phase‑1 + 4 cycles” check). |

### 4.1 Decode
For each lane `i`:
- `sign_A = A_lane[i][31]`
- `exp_A  = A_lane[i][30:23]`
- `mant_A = A_lane[i][22:0]`

Same for `B`.

Create a 24-bit significand:
- `sig_A = (exp_A==0) ? {1'b0, mant_A} : {1'b1, mant_A}`
- `sig_B = (exp_B==0) ? {1'b0, mant_B} : {1'b1, mant_B}`

### 4.2 Mantissa Multiplication
Compute:
- `mant_prod = sig_A * sig_B` (wide; at least 48 bits for 24x24)

**Submodule usage:**  
`multi12bX12b.v` can be used as a building block for a segmented multiplier (e.g., 12b×12b partial products) if the implementation decomposes 24×24 into smaller pieces. Using 12 bits multiplier, multiply a_low * b_low + a_high*b_low << 12 + a_low * b_high << 12 + a_high*b_high <<24 
	Obs: you can also multiply 24x24 bits as well, I selected 12x12.

### 4.3 Exponent Computation and Comparison
For each lane:
- `exp_prod = exp_A + exp_B - 127` for normalized inputs.
- For zeros/subnormals, behavior may treat them as zero contribution (implementation-defined but must be consistent).

Compute:
- `exp_max = maximum(exp_prod[i])` among lanes that are treated as valid/non-zero.

### 4.4 Alignment of Partial Products
For each lane:
- `shift = exp_max - exp_prod`
- Right shift partial product by `shift` so that all products share a common exponent domain.

**Submodule usage:**  
`Alignment_Shifter.v` performs the shifting of wide data by a shift amount and direction.

### 4.5 Sign Handling via Two's Complement
Each aligned product is converted into signed two's complement representation using:
- `sign_prod = sign_A XOR sign_B`

If `sign_prod==1`, negate the aligned magnitude:
- `signed_pp = (~aligned) + 1`

This allows all terms to be accumulated with a pure adder tree.

### 4.6 Adder Tree Reduction
Sum all signed partial products in a balanced tree to reduce latency and logic depth.

**Submodule usage:**
- `Adder_Tree.v` implements the multi-input reduction structure.
- `CLA_AdderTree.v`, `csla.v`, and `compressor7to2.v` may be used internally as adder primitives/accelerators.

### 4.7 Leading-One Detection and Normalization
After summation, determine:
- output sign = MSB of signed sum (or sign of the final signed value)
- magnitude = absolute value of signed sum

Then:
1. **Detect first '1'** (leading-one position) in the magnitude.
2. **Normalize** magnitude so that it becomes `1.xxxxx` in the target mantissa field.
3. Adjust exponent accordingly:
   - `exp_out = exp_max + normalization_adjust`

**Submodule usage:**
- `LZD.v` provides leading-zero/leading-one detection.

### 4.8 Packing Output
Construct FP32 result:
- `out_sign` (1 bit)
- `out_exp`  (8 bits)
- `out_mant` (23 bits)

Rounding policy may be implementation-defined for this generic spec; typical choices are truncation or round-to-nearest-even. If rounding is implemented, ensure it is synthesizable and deterministic.

## 5. Special-Case Behavior (Generic Guidance)

- only normalized numbers are taken into account.

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

## 7. Synthesizability Requirements
- No SystemVerilog Assertions (SVA) `property/sequence` syntax.
- Avoid non-synthesizable constructs in the RTL datapath (e.g., real numbers).
- Use `always_ff`/`always_comb` or `always @(*)`/`always @(posedge clk)` styles compatible with Icarus.
- All arrays and loops must be synthesizable (static bounds).
- Reset policy is implementation-defined; if no reset is present, ensure outputs are deterministically assigned before being sampled (e.g., via pipeline valid gating).

## 8. Provided Files
The implementation must keep these filenames/modules:
- Top: `pe_fp32.sv`
- Submodules:
  - `Adder_Tree.v`
  - `Alignment_Shifter.v`
  - `CEC.v`
  - `CLA_AdderTree.v`
  - `LZD.v`
  - `compressor7to2.v`
  - `csla.v`
  - `multi12bX12b.v`