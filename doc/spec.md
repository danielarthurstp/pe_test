# pe_fp32 — Specification


---

## 1. Overview

`pe_fp32` computes a **5‑lane FP32 dot product**:

`out = RoundFP32( Σ_{i=0..4} (A[i] × B[i]) )`
It is simply a processing element that computes the multiply and accumulate with rounding at the end using FP32 inputs.
- Inputs `A` and `B` each pack 5 IEEE‑754 binary32 lanes.

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

whenever A and B input is valid, clk_cntr is asserted during one cycle. This input only says that the data in A and B are valid. All signals are synchronous.


### 3.2 Output latency (as assumed by tests)

The cocotb driver samples `out` **4 cycles after Phase 1** (i.e., after the phase‑1 edge plus 4 additional rising edges).

---

## 4. Datapath breakdown (how pe_fp32.sv is divided)

`pe_fp32.sv` is organized into **three pipeline stages**, plus control pipelining:

| Stage | When active | What it does | Major outputs |
|---:|---|---|---|
| Stage 1 | `clk_cntr_stage1` | decode lanes, form segmented multiplier inputs, compute per-lane exponent/sign, and generate 5 partial products | `product[0..4]`, `d_exp0..d_exp4`, `ssign0..ssign4` |
| Stage 2 | combinational, gated by `clk_cntr_stage2` | exponent compare (max + diffs), align 5 partial products, sign-extend, accumulate via adder-tree (with optional feedback accumulator) | `ssum`, `mmax_exp_s2`, `sign_final` |
| Stage 3 | combinational, gated by `clk_cntr_stage3` | magnitude + leading-zero detect, normalize, RNE rounding, pack FP32 | `outt` (registered to `out`) |

---

## 5. Functional behavior (what math is implemented)

### 5.1 mantissa multiplication (stage 1)

Each FP32 significand is treated as `1.frac` (24-bit). The multiplication of these mantissas happens and later they are aligned to the highest exponent, two complemented depending on sign xor of lane and summed in later stages.

### 6.2 Exponent compare and alignment (stage 1)

- A single reference exponent `mmax_exp_s2` is computed across lanes.
- For each lane i, `diff_i = mmax_exp_s2 - d_exp_i`.
- the d_exp_i is calculated as: exponent_A_lane_i + exponent_B_lane_i - 10d'127
- Alignment shifts are performed by `Alignment_Shifter`:
  - Right shift by `diff_i` (exponent alignment)

### 6.3 Signed accumulation (stage 2)

Each aligned partial product is sign-extended and summed using the adder tree.
- The adder tree takes a sign bit per term (`s_sign[k]`).

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
  2. Wait 4 cycles
  3. Compare `out` to reference model (accumulate in high precision, round once at end)

---

## 8. Submodules (purpose, interface, and top-level connections)

This section explains **each provided RTL submodule** and **exactly how `pe_fp32.sv` connects to it**.

### 8.1 `multi12bX12b` — 12×12 segmented multiplier

- can be left unconnected, use a 24-bit multiplier for mantissa multiplication.


### 8.2 `CEC` — exponent compare / difference generator

**Purpose:** Computes:
- `max_exp`: maximum of the 5 per-lane product exponents
- `diff_0..diff_4`: per-lane difference to `max_exp` for alignment

**Interface:**
```verilog
module CEC(
    input  [9:0] exp_A_0, exp_A_1, exp_A_2, exp_A_3, exp_A_4,
    input  [9:0] exp_A_5, exp_A_6, exp_A_7, exp_A_8, exp_A_9,
    input  [9:0] exp_B_0, exp_B_1, exp_B_2, exp_B_3, exp_B_4,
    input  [9:0] exp_B_5, exp_B_6, exp_B_7, exp_B_8, exp_B_9,
    output [9:0] max_exp,
    output [9:0] diff_0, diff_1, diff_2, diff_3, diff_4,
    output [9:0] diff_5, diff_6, diff_7, diff_8, diff_9
);
```

**Top-level instantiation:**
```verilog
        CEC ec1(
            exp_A_0, exp_A_1, exp_A_2, exp_A_3, exp_A_4,
            exp_A_5, exp_A_6, exp_A_7, exp_A_8, exp_A_9,
            exp_B_0, exp_B_1, exp_B_2, exp_B_3, exp_B_4,
            exp_B_5, exp_B_6, exp_B_7, exp_B_8, exp_B_9,
            max_exp,
            diff_0, diff_1, diff_2, diff_3, diff_4,
            diff_5, diff_6, diff_7, diff_8, diff_9
        );
```

**Usage in top:**
- `mmax_exp_s2` is used in Stage 3 exponent normalizing.
- `diff_0..diff_4` are used to right shift the mantissa multiplication result per lane.

---

### 8.3 `Alignment_Shifter` — align a partial product

**Purpose:** Applies two shifts to each partial product:
1. `diff` (right shift): aligns all lanes to the common exponent `max_exp`. Simply received diff_0...diff_4.

**Interface:**
```verilog
module Alignment_Shifter(
    input  [47:0] n,
    input  [8:0]  diff,
    output [60:0] out
);
```

**Top-level connections (5 instances):**

| Instance | n | diff | out |
|---:|---|---|---|---|
| as0 | `product[0]` | `d_ddiff[0]` | `as_out[0]` |
| ... | ... | ... | ... | ... |
| as4 | `product[4]` | `d_ddiff[4]` | `as_out[4]` |

Obs: at top level, the as_out is two complemented depending of the sign xor.

as_out_2c[0] = sign_xor[0] ? ~as_out[0] + 1'b1; : as_out[0]
as_out_2c[1] = sign_xor[1] ? ~as_out[1] + 1'b1; : as_out[1]
as_out_2c[2] = sign_xor[2] ? ~as_out[2] + 1'b1; : as_out[2]
as_out_2c[3] = sign_xor[3] ? ~as_out[3] + 1'b1; : as_out[3]
as_out_2c[4] = sign_xor[4] ? ~as_out[4] + 1'b1; : as_out[4]

---

### 8.4 `Adder_Tree` — multi-input signed reduction with optional accumulator

**Purpose:** Sums 5 signed partial products and an optional accumulator term.

**Interface:**
```verilog
module Adder_Tree(
    input  [63:0] n1, n2, n3, n4, n5,
    input  [4:0]  sign,
    output [65:0] sum,
    output        sign_final
);
```

**Top-level connections:**
- `n1..n5` are derived from `as_out[k]` with sign-extension to 64 bits:
  - `n1 = {as_out_2c[0][60], as_out_2c[0][60], as_out_2c[0][60], as_out_2c[0]}`
  - ...
  - `n5 = {as_out_2c[4][60], as_out_2c[4][60], as_out_2c[4][60], as_out_2c[4]}`
- `sign[0..4]` is `s_sign[0..4]`
- outputs:
  - `sum -> ssum`
  - `sign_final -> sign_final`

---

### 8.5 `LZD` — leading-zero detector for normalization

**Purpose:** Finds the number of leading zeros in the 66-bit magnitude of the final sum so the result can be normalized.

**Interface:**
```verilog
`timescale 1ns / 1ps


module comp(in, zero_flag);
    input [10:0]in;
    output zero_flag;
    
    assign zero_flag = in?1'b0:1'b1;
    
endmodule


module mux2x1(S, I1, I0, out);
    input S;
    input [6:0]I0, I1;
    output [6:0]out;
    assign out = S?I1:I0;
endmodule



module LZD(sum, position);
    input [65:0]sum;
    output [6:0]position;
    
    wire zf1, zf2, zf3, zf4, zf5, zf6;
    wire [6:0]p1, p2, p3, p4, p5, p6;
    wire [6:0]w2, w3, w4, w5;
    
    comp i1(sum[10:0], zf1);
    comp i2(sum[21:11], zf2);
    comp i3(sum[32:22], zf3);
    comp i4(sum[43:33], zf4);
    comp i5(sum[54:44], zf5);
    comp i6(sum[65:55], zf6);
    
    assign p6 =  sum[65]?7'd1:(sum[64]?7'd2:(sum[63]?7'd3:(sum[62]?7'd4:(sum[61]?7'd5:(sum[60]?7'd6:(sum[59]?7'd7:(sum[58]?7'd8:(sum[57]?7'd9:(sum[56]?7'd10:7'd11)))))))));
    assign p5 =  sum[54]?7'd12:(sum[53]?7'd13:(sum[52]?7'd14:(sum[51]?7'd15:(sum[50]?7'd16:(sum[49]?7'd17:(sum[48]?7'd18:(sum[47]?7'd19:(sum[46]?7'd20:(sum[45]?7'd21:7'd22)))))))));
    assign p4 =  sum[43]?7'd23:(sum[42]?7'd24:(sum[41]?7'd25:(sum[40]?7'd26:(sum[39]?7'd27:(sum[38]?7'd28:(sum[37]?7'd29:(sum[36]?7'd30:(sum[35]?7'd31:(sum[34]?7'd32:7'd33)))))))));
    assign p3 =  sum[32]?7'd34:(sum[31]?7'd35:(sum[30]?7'd36:(sum[29]?7'd37:(sum[28]?7'd38:(sum[27]?7'd39:(sum[26]?7'd40:(sum[25]?7'd41:(sum[24]?7'd42:(sum[23]?7'd43:7'd44)))))))));
    assign p2 =  sum[21]?7'd45:(sum[20]?7'd46:(sum[19]?7'd47:(sum[18]?7'd48:(sum[17]?7'd49:(sum[16]?7'd50:(sum[15]?7'd51:(sum[14]?7'd52:(sum[13]?7'd53:(sum[12]?7'd54:7'd55)))))))));
    assign p1 =  sum[10]?7'd56:(sum[9]?7'd57:(sum[8]?7'd58:(sum[7]?7'd59:(sum[6]?7'd60:(sum[5]?7'd61:(sum[4]?7'd62:(sum[3]?7'd63:(sum[2]?7'd64:(sum[1]?7'd65:7'd66)))))))));

    mux2x1 m5(~zf6, p6, w5, position);
    mux2x1 m4(~zf5, p5, w4, w5);
    mux2x1 m3(~zf4, p4, w3, w4);
    mux2x1 m2(~zf3, p3, w2, w3);
    mux2x1 m1(~zf2, p2, p1, w2);
endmodule


```

**Top-level connection:**
- `in` is `sum_f[65:0]` where `sum_f` is the magnitude of the signed sum.
- `out` is `position` (normalization shift count).

THIS CODE MUST BE PRESENT at pe_fp32.sv . IT IS THE NORMALIZATION. DON'T CHANGE IT.
        // -----------------------------
        // Stage 3 signals (normalize + pack)
        // -----------------------------
        wire [6:0]  position;
        reg  [65:0] inter;
        reg  [31:0] outtt;
        reg  [31:0] outt;
        reg         carry_rounder;
		
        // -----------------------------
        // Stage 3
        // -----------------------------
        reg [22:0] mant_keep;
        reg        G, R, S;
        reg        lsb;
        reg        inc;
        reg [23:0] mant_rounded;  // 1 extra bit for carry
		assign sum_f = ssum;

        always @(*) begin
        if (clk_cntr_stage3 == 1'b0) begin // says that we are not in stage 3
            inter = 66'b0;
            outt  = 32'b0;
        end else begin // we are now in stage 3
            inter = sum_f << position;  // remove the leading 1 of accumulated sum.

            // Keep 23 bits (fraction field)
            mant_keep = inter[65:43];

            // Guard/round/sticky
            G = inter[42];
            R = inter[41];
            S = |inter[40:0];

            lsb = mant_keep[0];
            inc = G & (R | S | lsb);   // RNE

            mant_rounded = {1'b0, mant_keep} + inc;

            if (mant_rounded[23]) begin
            outt[22:0] = mant_rounded[23:1];  // shift right 1

            end else begin
            outt[22:0] = mant_rounded[22:0];
            end

            if (sum_f == 0) begin
            outt[30:23] = 0;
            end else if (position >= 20) begin
            outt[30:23] = mmax_exp_s2 - (position - 20);
            end else begin
            outt[30:23] = mmax_exp_s2 + (20 - position);
            end

            if (mant_rounded[23] && sum_f != 0)
            outt[30:23] = outt[30:23] + 1;

            outt[31] = ssignf;
        end

	// be aware that ssum is 66 bits wide.
END OF HINT
---

### 8.6 `CLA_AdderTree`, `csla`, `compressor7to2`

These files provide **adder/compressor building blocks** used internally by `Adder_Tree.v` (and can be reused by alternative architectures).

#### `compressor7to2`
- can be left unconnected

#### `csla`
- Purpose: can be left unconnected

#### `CLA_AdderTree`
- Purpose: can be left unconnected

---

## 9. Acceptance criteria

An implementation is compliant if:

1. It compiles and runs under **Icarus + cocotb** (no SVA properties/sequences).
2. It matches the cocotb reference model for finite normal inputs (round at the end).

